"""测试 app/utils/appium_caps.build_caps — 统一 Appium desired capabilities 构建。

保证基础 caps、可选字段、extra_caps 透传、强制 skip caps 四条契约。
"""

from app.utils.appium_caps import build_caps


def test_build_caps_base_defaults():
    caps = build_caps({})
    assert caps["platformName"] == "Android"
    assert caps["automationName"] == "UiAutomator2"
    assert caps["noReset"] is True
    assert caps["autoGrantPermissions"] is True


def test_build_caps_optional_fields_only_when_set():
    caps = build_caps({
        "automation_engine": "UiAutomator2",
        "app_package": "com.example.app",
        "app_activity": ".MainActivity",
        "device_name": "HonorPad",
        "platform_version": "12",
    })
    assert caps["appPackage"] == "com.example.app"
    assert caps["appActivity"] == ".MainActivity"
    assert caps["deviceName"] == "HonorPad"
    assert caps["platformVersion"] == "12"


def test_build_caps_optional_fields_absent_when_missing():
    caps = build_caps({})
    assert "appPackage" not in caps
    assert "appActivity" not in caps
    assert "deviceName" not in caps
    assert "platformVersion" not in caps


def test_build_caps_extra_caps_override():
    caps = build_caps({
        "automation_engine": "UiAutomator2",
        "extra_caps": {"appium:newCommandTimeout": 300, "someFlag": False},
    })
    assert caps["appium:newCommandTimeout"] == 300
    assert caps["someFlag"] is False


def test_build_caps_extra_caps_non_dict_ignored():
    caps = build_caps({"extra_caps": "not-a-dict"})
    assert caps.get("skipServerInstallation") is True


def test_build_caps_forces_skip_caps():
    # 即使 config 想禁掉 skip，仍强制开启（防厂商安全扫描卸载组件）
    caps = build_caps({"extra_caps": {"skipServerInstallation": False}})
    assert caps["skipServerInstallation"] is True
    assert caps["skipDeviceInitialization"] is True


def test_build_caps_does_not_mutate_input():
    config = {"app_package": "com.x.test", "extra_caps": {"k": "v"}}
    build_caps(config)
    assert config == {"app_package": "com.x.test", "extra_caps": {"k": "v"}}