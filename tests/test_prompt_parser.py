"""
Tests for classifier prompt parsing — especially the new format with explanation.
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from classifier import _parse_api_response, normalize_category


class TestParseApiResponse:
    """_parse_api_response: 处理 LLM 返回的 JSON 并提取类别+理由。"""

    def test_dict_format_with_explanation(self):
        """新格式: [{"类别":"...", "理由":"..."}] → 正确提取类别，忽略理由"""
        raw = json.dumps([
            {"类别": "自主学习/编程学习", "理由": "代码托管平台, 主要编程活动"},
            {"类别": "娱乐/视频", "理由": "视频分享网站"},
        ])
        result = _parse_api_response(raw, ["github.com", "youtube.com"])
        assert result == ["自主学习/编程学习", "娱乐/视频"]

    def test_dict_format_partial_explanation(self):
        """混合格式: 部分有条目带理由、部分不带"""
        raw = json.dumps([
            {"类别": "学校学习/课程/作业", "理由": "邮件服务"},
            {"类别": "其他/工具/搜索"},
        ])
        result = _parse_api_response(raw, ["mail.google.com", "translate.google.com"])
        assert result == ["学校学习/课程/作业", "其他/工具/搜索"]

    def test_string_format_backward_compatible(self):
        """旧格式兼容: ["一级/二级", ...] 仍正确解析"""
        raw = json.dumps(["自主学习/编程学习", "娱乐/视频"])
        result = _parse_api_response(raw, ["github.com", "youtube.com"])
        assert result == ["自主学习/编程学习", "娱乐/视频"]

    def test_invalid_json_returns_none(self):
        """非 JSON 返回 None"""
        result = _parse_api_response("不是 JSON", ["a"])
        assert result is None

    def test_wrong_length_returns_none(self):
        """数组长度不匹配返回 None"""
        raw = json.dumps(["自主学习/编程学习"])
        result = _parse_api_response(raw, ["a", "b"])
        assert result is None

    def test_dict_missing_category(self):
        """dict 缺少 '类别' 键 → 置为其他/待分类"""
        raw = json.dumps([{"理由": "只有理由"}])
        result = _parse_api_response(raw, ["unknown.app"])
        assert result == ["其他/待分类"]

    def test_custom_categories_with_explanation(self):
        """自定义分类体系下, 带理由的 dict 格式也能正确解析"""
        custom_l1 = frozenset({"工作/项目", "自主学习"})
        raw = json.dumps([
            {"类别": "工作/项目/写代码", "理由": "IDE 编程"},
        ])
        result = _parse_api_response(raw, ["vscode"], valid_l1=custom_l1)
        assert result == ["工作/项目/写代码"]


class TestNormalizeCategory:
    """normalize_category: 标准化和别名修正。"""

    def test_normal_standard(self):
        assert normalize_category("自主学习/编程学习") == "自主学习/编程学习"

    def test_known_alias_fixed(self):
        assert normalize_category("其他/工具搜索") == "其他/工具/搜索"

    def test_unknown_l1_defaults(self):
        assert normalize_category("健身/跑步") == "其他/待分类"

    def test_missing_l2_defaults(self):
        assert normalize_category("娱乐") == "其他/待分类"

    def test_custom_valid_l1_accepted(self):
        custom = frozenset({"工作/项目"})
        assert normalize_category("工作/项目/写代码", valid_l1=custom) == "工作/项目/写代码"

    def test_custom_valid_l1_rejected(self):
        custom = frozenset({"工作/项目"})
        assert normalize_category("娱乐/视频", valid_l1=custom) == "其他/待分类"
