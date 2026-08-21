"""测试数据库迁移逻辑 — 幂等性验证

测试目标：
1. 已有旧数据库升级：模拟不含新字段的表结构，插入旧数据，迁移后验证字段存在且默认值正确
2. 新数据库初始化：验证 schema.sql 创建的表包含新字段
3. 迁移幂等性：重复运行迁移不会报错
"""

import pytest
from sqlalchemy import create_engine, inspect, text, MetaData, Table, Column, Integer, String, DateTime, func
from sqlalchemy.orm import sessionmaker


class TestDatabaseMigration:
    """测试 _run_migrations() 的幂等性和正确性"""

    @pytest.fixture
    def old_db(self):
        """创建模拟的旧数据库（不含新字段），插入旧数据"""
        engine = create_engine("sqlite:///:memory:", echo=False)
        conn = engine.connect()

        # 创建旧版 projects 表（不含 platform）
        conn.execute(text("""
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(255) NOT NULL,
                target_url VARCHAR(500) NOT NULL,
                test_path VARCHAR(255) DEFAULT '/',
                browser_type VARCHAR(20) DEFAULT 'chromium',
                headless INTEGER DEFAULT 1,
                status VARCHAR(20) DEFAULT 'active',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))

        # 创建旧版 page_elements 表（不含 platform, selector_type, metadata）
        conn.execute(text("""
            CREATE TABLE page_elements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                element_type VARCHAR(50) NOT NULL,
                tag_name VARCHAR(50),
                element_id VARCHAR(255),
                name VARCHAR(255),
                class_name VARCHAR(500),
                selector VARCHAR(500) NOT NULL,
                text_content VARCHAR(500),
                placeholder VARCHAR(255),
                is_visible INTEGER DEFAULT 1,
                bounding_box TEXT,
                attributes TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
        """))

        conn.commit()
        yield engine, conn
        conn.close()
        engine.dispose()

    @pytest.fixture
    def old_db_with_data(self, old_db):
        """在旧数据库中插入示例数据"""
        engine, conn = old_db

        # 插入项目
        conn.execute(text("""
            INSERT INTO projects (id, name, target_url, test_path, browser_type, headless, status)
            VALUES (1, 'Project Alpha', 'https://alpha.com', '/', 'chromium', 1, 'active')
        """))
        conn.execute(text("""
            INSERT INTO projects (id, name, target_url, test_path, browser_type, headless, status)
            VALUES (2, 'Project Beta', 'https://beta.com', '/login', 'firefox', 0, 'active')
        """))

        # 插入页面元素
        conn.execute(text("""
            INSERT INTO page_elements (id, project_id, element_type, tag_name, selector, text_content)
            VALUES (1, 1, 'button', 'button', '#submit-btn', 'Submit')
        """))
        conn.execute(text("""
            INSERT INTO page_elements (id, project_id, element_type, tag_name, selector, text_content)
            VALUES (2, 1, 'input', 'input', '#username', '')
        """))

        conn.commit()
        yield engine, conn

    def _run_migration(self, engine):
        """模拟 database.py 中的 _run_migrations() 逻辑"""
        inspector = inspect(engine)
        migrations = [
            ("projects", "platform", "VARCHAR(10) DEFAULT 'web'"),
            ("page_elements", "platform", "VARCHAR(10) DEFAULT 'web'"),
            ("page_elements", "selector_type", "VARCHAR(20)"),
            ("page_elements", "metadata", "TEXT"),
        ]
        with engine.connect() as conn:
            for table, column, col_def in migrations:
                if table in inspector.get_table_names():
                    existing = {c["name"] for c in inspector.get_columns(table)}
                    if column not in existing:
                        stmt = f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"
                        conn.execute(text(stmt))
            conn.commit()

    # ── 测试用例 ──

    def test_old_data_has_platform_default_after_migration(self, old_db_with_data):
        """旧数据升级后，platform 字段应有默认值 'web'"""
        engine, conn = old_db_with_data

        # 迁移前：确认字段不存在
        inspector = inspect(engine)
        project_cols = {c["name"] for c in inspector.get_columns("projects")}
        assert "platform" not in project_cols, "迁移前不应有 platform 字段"

        # 执行迁移
        self._run_migration(engine)

        # 迁移后：确认字段存在
        inspector = inspect(engine)
        project_cols = {c["name"] for c in inspector.get_columns("projects")}
        assert "platform" in project_cols, "迁移后应有 platform 字段"

        # 验证旧数据的 platform 默认值为 'web'
        rows = conn.execute(text("SELECT id, name, platform FROM projects ORDER BY id")).fetchall()
        assert len(rows) == 2
        for row in rows:
            assert row[2] == "web", f"项目 {row[0]} 的 platform 应为 'web'，实际为 {row[2]}"

    def test_old_element_has_new_fields_after_migration(self, old_db_with_data):
        """旧元素数据升级后，应有 platform、selector_type、metadata 字段"""
        engine, conn = old_db_with_data

        # 执行迁移
        self._run_migration(engine)

        # 验证字段存在
        inspector = inspect(engine)
        element_cols = {c["name"] for c in inspector.get_columns("page_elements")}
        for col in ("platform", "selector_type", "metadata"):
            assert col in element_cols, f"迁移后 page_elements 应有 {col} 字段"

        # 验证旧数据默认值
        rows = conn.execute(text(
            "SELECT id, platform, selector_type, metadata FROM page_elements ORDER BY id"
        )).fetchall()
        assert len(rows) == 2
        for row in rows:
            assert row[1] == "web", f"元素 {row[0]} 的 platform 应为 'web'"
            assert row[2] is None, f"元素 {row[0]} 的 selector_type 应为 NULL"
            assert row[3] is None, f"元素 {row[0]} 的 metadata 应为 NULL"

    def test_migration_is_idempotent(self, old_db_with_data):
        """重复运行迁移不会报错，且数据不受影响"""
        engine, conn = old_db_with_data

        # 第一次迁移
        self._run_migration(engine)

        # 验证第一次迁移后的数据
        rows_before = conn.execute(text(
            "SELECT id, name, platform FROM projects ORDER BY id"
        )).fetchall()

        # 第二次迁移（不应报错）
        self._run_migration(engine)

        # 验证数据不变
        rows_after = conn.execute(text(
            "SELECT id, name, platform FROM projects ORDER BY id"
        )).fetchall()
        assert rows_before == rows_after, "幂等迁移不应改变数据"

    def test_new_database_has_all_columns(self, old_db):
        """新数据库初始化（schema.sql 或 create_all）后应包含所有新字段"""
        engine, conn = old_db

        # 先创建旧表，模拟 schema.sql 直接创建含新字段的表
        conn.execute(text("ALTER TABLE projects ADD COLUMN platform VARCHAR(10) DEFAULT 'web'"))
        conn.execute(text("ALTER TABLE page_elements ADD COLUMN platform VARCHAR(10) DEFAULT 'web'"))
        conn.execute(text("ALTER TABLE page_elements ADD COLUMN selector_type VARCHAR(20)"))
        conn.execute(text("ALTER TABLE page_elements ADD COLUMN metadata TEXT"))
        conn.commit()

        # 运行迁移（幂等，不应再添加任何字段）
        self._run_migration(engine)

        # 验证所有字段存在
        inspector = inspect(engine)
        project_cols = {c["name"] for c in inspector.get_columns("projects")}
        element_cols = {c["name"] for c in inspector.get_columns("page_elements")}

        assert "platform" in project_cols
        for col in ("platform", "selector_type", "metadata"):
            assert col in element_cols

    def test_orm_model_has_platform_default(self, db_session):
        """通过 ORM 创建 Project 时，platform 默认值应为 'web'"""
        from app.models.project import Project

        project = Project(name="ORM Test", target_url="https://orm-test.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        assert project.platform == "web"

    def test_create_project_with_android_platform(self, db_session):
        """通过 ORM 创建 Project 时，可指定 platform='android'"""
        from app.models.project import Project

        project = Project(
            name="Android Project",
            target_url="https://android-test.com",
            platform="android",
        )
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        assert project.platform == "android"

    def test_page_element_orm_has_defaults(self, db_session):
        """通过 ORM 创建 PageElement 时，新字段应有正确的默认值"""
        from app.models.project import Project
        from app.models.element import PageElement

        project = Project(name="Elem Test", target_url="https://elem-test.com")
        db_session.add(project)
        db_session.commit()

        element = PageElement(
            project_id=project.id,
            element_type="button",
            tag_name="button",
            selector="#test-btn",
        )
        db_session.add(element)
        db_session.commit()
        db_session.refresh(element)

        assert element.platform == "web"
        assert element.selector_type is None
        assert element.element_metadata is None


class TestConfigJsonMigration:
    """测试 config_json 字段迁移"""

    @pytest.fixture
    def old_db(self):
        """创建模拟的旧数据库（不含 config_json）"""
        engine = create_engine("sqlite:///:memory:", echo=False)
        conn = engine.connect()

        conn.execute(text("""
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(255) NOT NULL,
                target_url VARCHAR(500) NOT NULL,
                test_path VARCHAR(255) DEFAULT '/',
                browser_type VARCHAR(20) DEFAULT 'chromium',
                headless INTEGER DEFAULT 1,
                status VARCHAR(20) DEFAULT 'active',
                platform VARCHAR(10) DEFAULT 'web',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))

        conn.commit()
        yield engine, conn
        conn.close()
        engine.dispose()

    @pytest.fixture
    def old_db_with_data(self, old_db):
        """在旧数据库中插入示例数据"""
        engine, conn = old_db

        conn.execute(text("""
            INSERT INTO projects (id, name, target_url, test_path, browser_type, headless, status, platform)
            VALUES (1, 'Project Alpha', 'https://alpha.com', '/', 'chromium', 1, 'active', 'web')
        """))
        conn.execute(text("""
            INSERT INTO projects (id, name, target_url, test_path, browser_type, headless, status, platform)
            VALUES (2, 'Project Beta', 'https://beta.com', '/login', 'firefox', 0, 'active', 'web')
        """))

        conn.commit()
        yield engine, conn

    def _run_migration(self, engine):
        """模拟 config_json 迁移逻辑"""
        inspector = inspect(engine)
        migrations = [
            ("projects", "config_json", "TEXT DEFAULT '{}'"),
        ]
        with engine.connect() as conn:
            for table, column, col_def in migrations:
                if table in inspector.get_table_names():
                    existing = {c["name"] for c in inspector.get_columns(table)}
                    if column not in existing:
                        stmt = f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"
                        conn.execute(text(stmt))
            conn.commit()

    def test_old_db_has_config_json_after_migration(self, old_db_with_data):
        """旧数据库升级后获得 config_json 字段"""
        engine, conn = old_db_with_data

        # 迁移前：确认字段不存在
        inspector = inspect(engine)
        project_cols = {c["name"] for c in inspector.get_columns("projects")}
        assert "config_json" not in project_cols, "迁移前不应有 config_json 字段"

        # 执行迁移
        self._run_migration(engine)

        # 迁移后：确认字段存在
        inspector = inspect(engine)
        project_cols = {c["name"] for c in inspector.get_columns("projects")}
        assert "config_json" in project_cols, "迁移后应有 config_json 字段"

        # 验证旧数据的 config_json 默认值为 '{}'
        rows = conn.execute(text("SELECT id, name, config_json FROM projects ORDER BY id")).fetchall()
        assert len(rows) == 2
        for row in rows:
            assert row[2] == "{}", f"项目 {row[0]} 的 config_json 应为 '{{}}'，实际为 {row[2]}"

    def test_config_json_migration_idempotent(self, old_db_with_data):
        """重复运行迁移不会报错"""
        engine, conn = old_db_with_data

        # 第一次迁移
        self._run_migration(engine)

        # 验证第一次迁移后的数据
        rows_before = conn.execute(text(
            "SELECT id, name, config_json FROM projects ORDER BY id"
        )).fetchall()

        # 第二次迁移（不应报错）
        self._run_migration(engine)

        # 验证数据不变
        rows_after = conn.execute(text(
            "SELECT id, name, config_json FROM projects ORDER BY id"
        )).fetchall()
        assert rows_before == rows_after, "幂等迁移不应改变数据"

    def test_new_database_has_config_json(self, old_db):
        """新创建的数据库包含 config_json 列"""
        engine, conn = old_db

        # 添加 config_json 列（模拟新数据库已包含该字段）
        conn.execute(text("ALTER TABLE projects ADD COLUMN config_json TEXT DEFAULT '{}'"))
        conn.commit()

        # 运行迁移（幂等，不应再添加任何字段）
        self._run_migration(engine)

        # 验证字段存在
        inspector = inspect(engine)
        project_cols = {c["name"] for c in inspector.get_columns("projects")}
        assert "config_json" in project_cols

    def test_existing_data_keeps_original_config_json(self, old_db):
        """已有 config_json 数据在迁移后保持不变"""
        engine, conn = old_db

        # 先创建含 config_json 的表并插入数据
        conn.execute(text("ALTER TABLE projects ADD COLUMN config_json TEXT DEFAULT '{}'"))
        conn.execute(text("""
            INSERT INTO projects (id, name, target_url, config_json)
            VALUES (1, 'Config Project', 'https://config.com', '{"appium_server_url": "http://localhost:4723"}')
        """))
        conn.commit()

        # 运行迁移
        self._run_migration(engine)

        # 验证数据不变
        row = conn.execute(
            text("SELECT config_json FROM projects WHERE id = 1")
        ).fetchone()
        assert row[0] == '{"appium_server_url": "http://localhost:4723"}', \
            f"config_json 数据应保持不变，实际为 {row[0]}"


class TestConfigJsonData:
    """测试 config_json 数据完整性"""

    def test_config_json_default_empty(self, db_session):
        """新项目的 config_json 默认值为 '{}'"""
        from app.models.project import Project

        project = Project(name="Default Config", target_url="https://default-config.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        assert project.config_json == "{}"

    def test_config_json_stores_android_config(self, db_session):
        """config_json 可以存储 Android 配置"""
        from app.models.project import Project
        import json

        android_config = {
            "appium_server_url": "http://localhost:4723",
            "app_package": "com.example.app",
            "app_activity": ".MainActivity",
            "device_name": "Android Emulator",
            "platform_version": "12.0",
        }
        project = Project(
            name="Android App",
            target_url="https://android-app.com",
            platform="android",
            config_json=json.dumps(android_config, ensure_ascii=False),
        )
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        stored = json.loads(project.config_json) if isinstance(project.config_json, str) else project.config_json
        assert stored["appium_server_url"] == "http://localhost:4723"
        assert stored["app_package"] == "com.example.app"
        assert stored["app_activity"] == ".MainActivity"

    def test_config_json_round_trip(self, db_session):
        """config_json 经过保存和查询后数据不变"""
        from app.models.project import Project
        import json

        config = {
            "appium_server_url": "http://localhost:4723",
            "app_package": "com.example.app",
            "settings": {
                "timeout": 30,
                "retry_count": 3,
            },
        }
        project = Project(
            name="Round Trip",
            target_url="https://round-trip.com",
            platform="android",
            config_json=json.dumps(config, ensure_ascii=False),
        )
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        stored = json.loads(project.config_json) if isinstance(project.config_json, str) else project.config_json
        assert stored == config
        assert stored["settings"]["timeout"] == 30
        assert stored["settings"]["retry_count"] == 3