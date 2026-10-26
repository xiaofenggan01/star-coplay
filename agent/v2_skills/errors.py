"""
v2_skills.errors — L3 SkillRuntime 异常类。
"""


class BridgeError(Exception):
    """L1/L2 IPC 调用失败。

    当 Mod 侧返回 success=false 时，SkillContext 的查询方法
    会将错误信息包装为此异常抛出。
    """

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


class SkillCancelled(Exception):
    """技能被外部取消。

    当 SkillContext.check_cancelled() 检测到取消标记时抛出，
    SkillRunner 捕获后返回 partial 状态。
    """

    pass


class SkillLoadError(Exception):
    """技能加载失败。

    在 SkillRegistry 加载技能时，如果 skill.md 解析失败、
    script.py 导入失败或函数签名不匹配，抛出此异常。
    """

    pass
