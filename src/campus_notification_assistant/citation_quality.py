"""Limited citation guards; word matches are not general semantic verification."""

import re

from .models import CitedText, Item, Material

ACTION_NAMES = ("报名", "申请", "办理", "领取")
ACTION_OR_REQUEST = re.compile(
    r"报名|申请|办理|领取|领用|请|须|必须|应当|需要|提交|上传|缴费|登记|预约|注册|递交|报到|取|发放"
)
BACKGROUND = re.compile(r"举行|举办|比赛信息|活动介绍|通知|将于|地点|时间")
SENTENCE_BOUNDARY = re.compile(r"[。！？!?；;\r\n]")
MAX_ADDED_CONTEXT = 512


def reject_background_action_quote(action: CitedText) -> CitedText:
    """Flag known action labels citing only descriptive background.

    Unknown action wording and paraphrased requests are left for user review.
    Never invent a replacement quote, and never reinterpret user content.
    """
    if (
        action.origin == "extracted"
        and action.review == "matched"
        and any(name in action.value for name in ACTION_NAMES)
        and BACKGROUND.search(action.evidence)
        and not ACTION_OR_REQUEST.search(action.evidence)
    ):
        return action.model_copy(update={"review": "invalid"})
    return action


def complete_material_citations(item: Item, source: str) -> Item:
    """Add bounded literal sentence context to fresh, precisely located AI quotes.

    This preserves a shared subject within a sentence. It does not infer conditions
    from other sentences, repair invalid spans or alter user-selected citations.
    """

    def complete(material: Material) -> Material:
        start, end = material.evidence_start, material.evidence_end
        if (
            material.origin != "extracted"
            or material.review != "matched"
            or start is None
            or end is None
        ):
            return material
        before = list(SENTENCE_BOUNDARY.finditer(source, 0, start))
        left = before[-1].end() if before else 0
        tail = source[start:end].rstrip(' \t”’"）)】》')
        if tail and SENTENCE_BOUNDARY.fullmatch(tail[-1]):
            right = end
        else:
            after = SENTENCE_BOUNDARY.search(source, end)
            right = after.end() if after else len(source)
        if start - left + right - end > MAX_ADDED_CONTEXT:
            return material
        return material.model_copy(
            update={
                "evidence": source[left:right],
                "evidence_start": left,
                "evidence_end": right,
            }
        )

    return item.model_copy(update={"materials": tuple(complete(m) for m in item.materials)})
