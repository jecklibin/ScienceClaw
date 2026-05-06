RPA_RELAXED_CHROMIUM_ARGS = [
    "--disable-cache", # 禁用缓存，确保每次请求都是最新的
    # "--start-maximized", # 启动时最大化窗口
    "--activate-on-launch", # 启动后自动激活窗口
    "--disable-features=MediaRouter,WebUsb,WebHid,Serial,Discovery,NetworkPrediction", # 禁用不必要的功能，减少被检测的风险
    "--disable-background-networking", # 禁用后台网络请求，减少被检测的风险
    "--disable-client-side-phishing-detection", # 禁用客户端钓鱼检测，减少被检测的风险
    "--disable-features=IsolateOrigins,site-per-process", # 禁用站点隔离，允许跨域访问
    "--disable-web-security", # 禁用同源策略，允许跨域请求
    "--allow-running-insecure-content", # 允许加载不安全的内容
    "--disable-features=PermissionsAPI" # 关键：禁用权限请求弹窗
]

RPA_CONTEXT_KWARGS = {
    "no_viewport": True,
    "accept_downloads": True,
    "ignore_https_errors": True,
}


def get_chromium_launch_kwargs(*, headless: bool) -> dict:
    return {
        "headless": headless,
        "args": list(RPA_RELAXED_CHROMIUM_ARGS),
    }


def get_context_kwargs(**overrides) -> dict:
    kwargs = dict(RPA_CONTEXT_KWARGS)
    kwargs.update(overrides)
    return kwargs
