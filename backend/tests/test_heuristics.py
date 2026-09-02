"""启发式推断的兼容性测试：任意形状列表路径、中文字段键、投递列表特征打分。

这些形态在固定候选路径 + 纯拉丁正则的旧实现里全部 miss——
即用户反馈的「无法从该采样生成配方：未找到可提取的投递列表数据」的主因。
"""

from app.llm import heuristics


def test_generic_scan_finds_uncommon_list_path():
    """自研接口形状 data.pageData.applyRecords：固定候选未命中 → 通用递归扫描兜底。"""
    data = {
        "code": 0, "msg": "ok",
        "data": {"pageData": {"applyRecords": [
            {"positionName": "后端工程师", "applyStatusText": "简历评估中",
             "deliverTime": "2026-08-01", "applyId": "a1"},
            {"positionName": "算法工程师", "applyStatusText": "笔试中",
             "deliverTime": "2026-08-02", "applyId": "a2"},
        ]}},
    }
    assert heuristics.derive_list_json_path(data) == "data.pageData.applyRecords"
    items = heuristics.locate_list(data)
    assert items and len(items) == 2


def test_generic_scan_prefers_apply_list_over_noise():
    """同一响应里有多个数组：banner/推荐职位没有逐条申请状态 → 必须选投递列表。"""
    data = {"data": {
        "banners": [{"title": "秋招启动", "img": "x"}] * 3,
        "recommendJobs": [{"jobTitle": "前端", "city": "北京"}] * 5,
        "myApplications": [{"jobTitle": "后端", "applyStatus": "评估中", "applyId": "1"},
                           {"jobTitle": "算法", "applyStatus": "已投递", "applyId": "2"}],
    }}
    assert heuristics.derive_list_json_path(data) == "data.myApplications"


def test_chinese_field_keys_build_recipe():
    """央国企/自研站常见中文键：岗位名称/投递状态/工作地点。"""
    data = {"code": 0, "data": {"list": [
        {"岗位名称": "数据开发工程师", "投递状态": "已投递", "投递时间": "2026-08-20",
         "工作地点": "杭州", "id": "1001"},
        {"岗位名称": "风控算法工程师", "投递状态": "简历评估中", "投递时间": "2026-08-22",
         "工作地点": "上海", "id": "1002"},
    ]}}
    out = heuristics.build_recipe(
        "https://hr.example.cn/api/my/apply?page=1", "GET", data,
        "https://hr.example.cn/myapply",
    )
    assert out is not None
    fmap = {k: v.json_path for k, v in out.recipe.field_map.items()}
    assert fmap["job_title"] == "岗位名称"
    assert fmap["status_raw"] == "投递状态"
    assert fmap["work_location"] == "工作地点"
    assert fmap["applied_at"] == "投递时间"
    assert out.observations[0].job_title == "数据开发工程师"


def test_status_required_guard_keeps_wrong_list_out():
    """有 title 无 status 的数组（纯职位列表）绝不能被当作投递列表。

    data.list 是固定候选路径且排在前，但缺 status → 必须让位给通用扫描找到
    的 data.applyList（有 status）；全部数组都缺 status 时才返回 None。
    """
    data = {"data": {
        "list": [{"positionName": "前端工程师", "cityName": "北京"}] * 4,
        "applyList": [
            {"positionName": "后端工程师", "applyStatusName": "评估中"}],
    }}
    out = heuristics.build_recipe(
        "https://hr.example.cn/api/x", "GET", data, "https://hr.example.cn/myapply")
    assert out is not None
    assert out.recipe.list_source.list_json_path == "data.applyList"
    assert out.observations[0].job_title == "后端工程师"

    # 所有数组都没有 status 字段 → 宁缺毋错，返回 None
    pure_jobs = {"data": {"list": [
        {"positionName": "前端工程师", "cityName": "北京"}] * 4}}
    assert heuristics.build_recipe(
        "https://hr.example.cn/api/x", "GET", pure_jobs, "https://hr.example.cn/myapply"
    ) is None
