import pytest
import asyncio
from backend.app.agents import ResponseEvaluator

class DummyLLM:
    async def ainvoke(self, prompt):
        class R:
            content = "True" if "incomplete" in prompt else "False"
        return R()

@pytest.mark.asyncio
async def test_response_evaluator_should_use_tool():
    evaluator = ResponseEvaluator(DummyLLM())
    assert await evaluator.should_use_tool("What is the max altitude?", "I don't know") is True
    assert await evaluator.should_use_tool("What is the max altitude?", "The max altitude was 100m") is False 