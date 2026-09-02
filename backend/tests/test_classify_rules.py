"""T2 状态兜底分类测试：LLM 一次分类 → 规则缓存 → 全平台复用不再调用。"""

import pytest

from app.db.models import Portal, StatusRule
from app.llm import classify
from app.llm.schemas import ClassifyOutput


@pytest.fixture()
def portal(db):
    row = Portal(name="X 校招", company="X", provider_key="recipe", domains=["x.com"],
                 enabled=True, config={"recipe": {}, "status_map": []})
    db.add(row)
    db.commit()
    return row


def test_rule_table_takes_precedence(db, portal):
    db.add(StatusRule(scope_type="portal", scope_key=str(portal.id), pattern="^神秘状态$",
                      mapped_status="interview_unknown", priority=10, source="manual"))
    db.commit()
    assert classify.resolve_status(db, portal, "神秘状态") == "interview_unknown"


def test_recipe_status_map_and_generic_fallback(db, portal):
    portal.config["status_map"] = [{"pattern": "^2$", "status": "screening"}]
    assert classify.resolve_status(db, portal, "2") == "screening"        # 配方映射
    assert classify.resolve_status(db, portal, "面试安排中") == "interview_unknown"  # 通用兜底


def test_llm_classify_caches_rule_and_calls_once(db, portal, monkeypatch):
    calls = []

    def fake_classify(sess, desc, text, sample_id=None):
        calls.append(text)
        return ClassifyOutput(status="written_test", confidence=0.95, reason="状态文案甲")

    monkeypatch.setattr(classify.providers, "classify_status", fake_classify)

    assert classify.resolve_status(db, portal, "状态文案甲") == "written_test"
    assert len(calls) == 1
    # 规则已缓存：第二次解析不再调用 LLM
    assert classify.resolve_status(db, portal, "状态文案甲") == "written_test"
    assert len(calls) == 1

    rule = db.scalar(__import__("sqlalchemy").select(StatusRule).where(StatusRule.source == "llm"))
    assert rule is not None and rule.mapped_status == "written_test"
    assert rule.pattern == "^状态文案甲$"


def test_llm_low_confidence_falls_to_pending(db, portal, monkeypatch):
    monkeypatch.setattr(
        classify.providers, "classify_status",
        lambda *a, **kw: ClassifyOutput(status="rejected", confidence=0.4, reason="不确定"),
    )
    assert classify.resolve_status(db, portal, "状态文案乙") == "pending_confirm"
    assert db.query(StatusRule).count() == 0  # 不确定不写规则


def test_llm_ambiguous_and_error_degrade(db, portal, monkeypatch):
    monkeypatch.setattr(
        classify.providers, "classify_status",
        lambda *a, **kw: ClassifyOutput(status="ambiguous", confidence=0.9, reason="分不清"),
    )
    assert classify.resolve_status(db, portal, "某些文案") == "pending_confirm"

    def boom(*a, **kw):
        raise RuntimeError("LLM 故障")

    monkeypatch.setattr(classify.providers, "classify_status", boom)
    assert classify.resolve_status(db, portal, "状态文案丙") == "pending_confirm"  # 不阻塞同步
