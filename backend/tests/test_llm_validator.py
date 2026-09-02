"""回放验证器单元测试：七条断言逐条构造正反例（LLM_DESIGN.md §2.4）。"""

import json
from pathlib import Path

from app.llm import validator
from app.llm.schemas import (
    AuthSpec,
    Condition,
    FieldMapping,
    ObservedApplication,
    RecipeGenOutput,
    RecipeSpec,
    StatusMapEntry,
    XHRSource,
)

GOLDEN = json.loads((Path(__file__).parent / "golden_samples" / "tencent_like.json").read_text(encoding="utf-8"))
SAMPLE_URL = GOLDEN["sample"]["url"]
SAMPLE_DOM = GOLDEN["sample"]["dom"]
NETWORK = GOLDEN["sample"]["network"]


def make_output(**overrides) -> RecipeGenOutput:
    recipe = RecipeSpec(
        auth=AuthSpec(
            login_success=Condition(url_contains=["progress.html"]),
            session_invalid=Condition(url_contains=["login"]),
        ),
        list_source=XHRSource(
            url_pattern="https://join.qq.com/api/v1/apply/getApplyProcess*",
            list_json_path="data",
        ),
        field_map={
            "job_title": FieldMapping(json_path="positionInfo.applyPositionTxt"),
            "status_raw": FieldMapping(json_path="currentStatus.status"),
            "applied_at": FieldMapping(json_path="applyTime", required=False),
        },
        status_map=[{"pattern": "^2$", "status": "screening"}],
        meta={"generated_by": "test"},
    )
    output = RecipeGenOutput(
        recipe=recipe,
        observations=[ObservedApplication(job_title="后端开发工程师（腾讯云）", status_raw="2")],
        unmapped_status_texts=[],
        confidence=0.9,
    )
    for key, value in overrides.items():
        setattr(output, key, value)
    return output


def test_golden_recipe_passes_all_assertions():
    verdict = validator.replay(make_output(), SAMPLE_URL, SAMPLE_DOM, NETWORK)
    assert verdict.ok, verdict.errors
    assert verdict.stats["records"] == 1
    rec = verdict.records[0]
    assert rec.job_title == "后端开发工程师（腾讯云）"
    assert rec.status_raw == "2"
    assert verdict.stats["matched_xhr"].startswith("https://join.qq.com/api/v1/apply/getApplyProcess")


def test_assertion1_unmatched_url_pattern():
    output = make_output()
    output.recipe.list_source = XHRSource(url_pattern="https://join.qq.com/api/nonexistent*")
    verdict = validator.replay(output, SAMPLE_URL, SAMPLE_DOM, NETWORK)
    assert not verdict.ok
    assert any("断言1" in e for e in verdict.errors)


def test_assertion2_hallucinated_field_path():
    output = make_output()
    output.recipe.field_map["job_title"] = FieldMapping(json_path="positionInfo.notExist")
    verdict = validator.replay(output, SAMPLE_URL, SAMPLE_DOM, NETWORK)
    assert not verdict.ok
    assert any("断言2" in e for e in verdict.errors)


def test_assertion3_uncovered_status_and_bad_regex():
    # 数字码 "2" 不被 status_map 覆盖且未声明兜底 → 失败
    output = make_output()
    output.recipe.status_map = []
    verdict = validator.replay(output, SAMPLE_URL, SAMPLE_DOM, NETWORK)
    assert not verdict.ok
    assert any("断言3" in e for e in verdict.errors)

    # 显式声明留给兜底 → 通过（数字码语义不猜，运行期沉淀）
    output.recipe.status_map = []
    output.unmapped_status_texts = ["2"]
    verdict = validator.replay(output, SAMPLE_URL, SAMPLE_DOM, NETWORK)
    assert verdict.ok, verdict.errors

    # 声明了不存在的原文 → 禁止编造
    output.unmapped_status_texts = ["2", "不存在的状态"]
    verdict = validator.replay(output, SAMPLE_URL, SAMPLE_DOM, NETWORK)
    assert not verdict.ok

    # 坏正则 → 失败
    output.unmapped_status_texts = ["2"]
    output.recipe.status_map = [{"pattern": "([", "status": "screening"}]
    verdict = validator.replay(output, SAMPLE_URL, SAMPLE_DOM, NETWORK)
    assert not verdict.ok
    assert any("非法正则" in e for e in verdict.errors)


def test_assertion4_observation_mismatch():
    output = make_output()
    output.observations = [ObservedApplication(job_title="编造的岗位", status_raw="2")]
    verdict = validator.replay(output, SAMPLE_URL, SAMPLE_DOM, NETWORK)
    assert not verdict.ok
    assert any("断言4" in e for e in verdict.errors)


def test_assertion6_login_conditions_must_discriminate():
    output = make_output()
    output.recipe.auth = AuthSpec(
        login_success=Condition(url_contains=["not-in-url"]),
        session_invalid=Condition(url_contains=[]),
    )
    verdict = validator.replay(output, SAMPLE_URL, SAMPLE_DOM, NETWORK)
    assert not verdict.ok
    assert any("断言6" in e for e in verdict.errors)

    # session_invalid 在已登录采样上成立 → 无法区分
    output.recipe.auth = AuthSpec(
        login_success=Condition(url_contains=["progress.html"]),
        session_invalid=Condition(url_contains=["progress"]),
    )
    verdict = validator.replay(output, SAMPLE_URL, SAMPLE_DOM, NETWORK)
    assert not verdict.ok


def test_assertion7_user_identifier_baked_in():
    output = make_output()
    # 把采样用户的 resumeId 值（123456789）烙进 URL → 必须参数化
    output.recipe.list_source = XHRSource(
        url_pattern="https://join.qq.com/api/v1/apply/getApplyProcess?resumeId=123456789",
        list_json_path="data",
    )
    verdict = validator.replay(output, SAMPLE_URL, SAMPLE_DOM, NETWORK)
    assert not verdict.ok
    assert any("断言7" in e and "123456789" in e for e in verdict.errors)


def test_assertion7_placeholder_must_be_declared():
    # 采样中存在带用户段的同型接口，配方才能引用它
    network = NETWORK + [
        {
            "url": "https://join.qq.com/api/v1/user/99887766/apply/getApplyProcess",
            "method": "GET",
            "params": {},
            "request_body": "",
            "response_body": NETWORK[0]["response_body"],
        }
    ]
    output = make_output()
    output.recipe.list_source = XHRSource(
        url_pattern="https://join.qq.com/api/v1/user/{{user_id}}/apply/getApplyProcess",
        list_json_path="data",
    )
    verdict = validator.replay(output, SAMPLE_URL, SAMPLE_DOM, network)
    assert not verdict.ok
    assert any("user_id" in e for e in verdict.errors)

    # 声明了解析方式 → 参数化合规（cookie 解析）
    output.recipe.runtime_params = {
        "user_id": {"type": "cookie", "name": "jc_uid"},
    }
    verdict = validator.replay(output, SAMPLE_URL, SAMPLE_DOM, network)
    assert verdict.ok, verdict.errors


def test_url_pattern_compile_wildcard_and_placeholder():
    regex = validator.compile_url_pattern("https://x.com/api/list*")
    assert regex.search("https://x.com/api/list?t=123")
    assert not regex.search("https://x.com/api/other")

    regex = validator.compile_url_pattern("https://x.com/u/{{uid}}/list")
    m = regex.search("https://x.com/u/99887766/list")
    assert m and m.group("uid") == "99887766"
