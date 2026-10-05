"""Appium desired capabilities 统一构建 — 消灭「新入口漏合并 extra_caps」的隐患

所有需要建立 Appium 会话的路径（执行 / 自愈 / 元素抓取）都必须从这里构建 desired_caps，
保证：
  1. 平台基础 caps + 可选 app/device/platformVersion 字段一致；
  2. config_json["extra_caps"] 一律透传覆盖默认值；
  3. skipServerInstallation / skipDeviceInitialization 无条件强制开启，
     防止荣耀等厂商安全扫描在每次会话里反复检查/卸载组件导致死循环。

调用方拿到 dict 后经 AppiumOptions().load_capabilities(caps) 传入 Remote(options=...)。
"""

from typing import Any, Mapping


def build_caps(config: Mapping[str, Any]) -> dict[str, Any]:
    """从项目 config_json 构建 Appium desired capabilities。

    config 已解析为 dict（调用方保证）；返回新的 dict，不修改入参。
    """
    caps: dict[str, Any] = {
        "platformName": "Android",
        # 主执行/自愈可能配置其它引擎（默认 UiAutomator2）；抓取路径也允许覆盖
        "automationName": config.get("automation_engine", "UiAutomator2"),
        "noReset": True,
        "autoGrantPermissions": True,
    }

    # 只添加非空配置项
    if config.get("app_package"):
        caps["appPackage"] = config["app_package"]
    if config.get("app_activity"):
        caps["appActivity"] = config["app_activity"]
    if config.get("device_name"):
        caps["deviceName"] = config["device_name"]
    if config.get("platform_version"):
        caps["platformVersion"] = config["platform_version"]

    # config_json["extra_caps"] 显式追加的 capability，透传覆盖默认值
    extra = config.get("extra_caps")
    if isinstance(extra, dict):
        caps.update(extra)

    # 全局兜底：跳过组件安装/初始化，防厂商安全扫描反复卸载组件（强制，不被覆盖）
    caps["skipServerInstallation"] = True
    caps["skipDeviceInitialization"] = True

    return caps