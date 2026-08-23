-- ============================================================
-- AutoPilot Database Schema (MySQL / SQLite 双兼容)
-- 先创建数据库: CREATE DATABASE autopilot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- 所有外键 ON DELETE CASCADE，删除项目级联删除所有关联数据
-- ============================================================

-- 1. 项目表
CREATE TABLE IF NOT EXISTS projects (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    target_url VARCHAR(500) NOT NULL,
    test_path VARCHAR(255) DEFAULT '/',
    browser_type VARCHAR(20) DEFAULT 'chromium',
    headless TINYINT DEFAULT 1,
    status VARCHAR(20) DEFAULT 'active',          -- active / archived
    platform VARCHAR(10) DEFAULT 'web',           -- web / android (创建后只读)
    config_json TEXT,                              -- JSON: 平台配置 (Android: appium_server_url, app_package 等)
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. 页面元素表
CREATE TABLE IF NOT EXISTS page_elements (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    element_type VARCHAR(50) NOT NULL,            -- button / input / link / select / textarea
    tag_name VARCHAR(50),
    element_id VARCHAR(255),
    name VARCHAR(255),
    class_name VARCHAR(500),
    selector VARCHAR(500) NOT NULL,               -- Playwright selector
    text_content VARCHAR(500),
    placeholder VARCHAR(255),
    is_visible TINYINT DEFAULT 1,
    bounding_box TEXT,                            -- JSON: {x, y, width, height}
    attributes TEXT,                              -- JSON
    platform VARCHAR(10) DEFAULT 'web',           -- web / android
    selector_type VARCHAR(20),                    -- css / xpath / resource_id (NULL 兼容历史数据)
    metadata TEXT,                                -- JSON (Android 专用: resource_id, content_desc, class_name, text, bounds)
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    INDEX idx_pe_project_id (project_id),
    INDEX idx_pe_element_type (element_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. 测试用例表
CREATE TABLE IF NOT EXISTS test_cases (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    case_name VARCHAR(255) NOT NULL,
    case_no VARCHAR(50),                          -- Excel case number
    priority VARCHAR(10) DEFAULT 'P1',            -- P0 / P1 / P2 / P3
    pre_condition TEXT,
    steps TEXT NOT NULL,                          -- JSON array
    expected_result TEXT,
    source_excel VARCHAR(255),                    -- Source Excel filename
    excel_row INT,                                -- Excel row number
    status VARCHAR(20) DEFAULT 'pending',         -- pending / imported / generated
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    INDEX idx_tc_project_id (project_id),
    INDEX idx_tc_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4. 生成代码表
CREATE TABLE IF NOT EXISTS generated_codes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    case_id INT NOT NULL,
    code_content TEXT NOT NULL,
    code_language VARCHAR(20) DEFAULT 'python',
    generation_prompt TEXT,
    ai_model VARCHAR(50),
    is_valid TINYINT DEFAULT 0,
    syntax_error TEXT,
    is_healed TINYINT DEFAULT 0,                 -- Is healed code
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
    INDEX idx_gc_case_id (case_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5. 执行批次表
CREATE TABLE IF NOT EXISTS executions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    batch_name VARCHAR(255),
    total_cases INT DEFAULT 0,
    passed_cases INT DEFAULT 0,
    failed_cases INT DEFAULT 0,
    status VARCHAR(20) DEFAULT 'running',         -- running / healing / completed / stopped / failed
    start_time DATETIME,
    end_time DATETIME,
    execution_mode VARCHAR(20) DEFAULT 'headless',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 6. 执行步骤表
CREATE TABLE IF NOT EXISTS execution_steps (
    id INT AUTO_INCREMENT PRIMARY KEY,
    execution_id INT NOT NULL,
    case_id INT NOT NULL,
    step_index INT NOT NULL,
    action VARCHAR(50),                           -- click / fill / navigate / select / hover / scroll
    target_selector VARCHAR(500),
    input_value TEXT,
    status VARCHAR(20) DEFAULT 'pending',         -- pending / success / failed / skipped
    screenshot_before VARCHAR(500),               -- Screenshot path
    screenshot_after VARCHAR(500),
    log_output TEXT,
    error_message TEXT,
    exception_type VARCHAR(100),
    duration_ms INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES executions(id) ON DELETE CASCADE,
    FOREIGN KEY (case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
    INDEX idx_es_execution_id (execution_id),
    INDEX idx_es_case_id (case_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 7. 执行报告表
CREATE TABLE IF NOT EXISTS execution_reports (
    id INT AUTO_INCREMENT PRIMARY KEY,
    execution_id INT NOT NULL UNIQUE,
    report_html TEXT,                             -- Full HTML report (inline resources)
    report_summary TEXT,                          -- JSON: {total, passed, failed, pass_rate, duration}
    download_url VARCHAR(500),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES executions(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 8. 自愈记录表
CREATE TABLE IF NOT EXISTS heal_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    execution_step_id INT NOT NULL,
    original_code TEXT,
    error_context TEXT,                           -- JSON: {error_msg, screenshot, dom_snapshot}
    healed_code TEXT,
    heal_prompt TEXT,
    retry_status VARCHAR(20) DEFAULT 'pending',   -- pending / success / failed
    retry_count INT DEFAULT 0,
    attempts TEXT,                                -- JSON: [{attempt, generated_code, status, error, created_at}]
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (execution_step_id) REFERENCES execution_steps(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;