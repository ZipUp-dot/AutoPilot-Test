"""用例步骤数据模型"""

from pydantic import BaseModel


class TestStep(BaseModel):
    step_number: int
    action: str
    target: str = ""
    value: str = ""
    description: str = ""
