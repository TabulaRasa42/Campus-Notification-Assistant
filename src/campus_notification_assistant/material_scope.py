"""Conservative current-item stage and explicit shared-subject handling.

This is not semantic verification: unknown or mixed contexts are left for review.
Only fresh AI drafts use this guard; saved records and user corrections are not rewritten.
"""

import re

from .models import Item, Material

BOUNDARY = re.compile(r"[。！？!?；;\n]")
REGISTRATION = re.compile(r"报名(?!表|单|材料|资格|者|人|学生|对象|后|成功)")
APPLICATION = re.compile(r"申请(?!表|书|单|材料|资格|者|人|学生|对象|后|成功)")
OTHER_STAGE_REQUIREMENT = re.compile(
    r"(?:参加比赛|(?:报到|检录|领取)(?:时|前|现场))"
    r"[（(，,：:\s]*(?:请|须|需|必须|应|自行|提前|务必)*"
    r"(?:携带|带|提交|准备)[^。！？!?；;\n，,：:（）()]*$"
)
SHARED_SUBJECT = re.compile(
    r"^(?:\s*【[^】\r\n]*】\s*)?(?:请\s*)?"
    r"(?P<subject>(?:所有|全体)[^，,。！？!?；;\r\n]{0,24}?(?:人员|同学|学生|者|人))"
    r"(?=于|在|均|都|须|需|应|报名时|申请时|办理时|领取时)"
)
PARTICULAR_SCOPE = re.compile(
    r"其中|首次|往年|部分|仅|只有|限于|若|如果|如有|免|不需|无需|除|另|这些|上述"
)
PREDICATE_START = re.compile(
    r"^(?:(?:报名|申请|办理|领取)时)?"
    r"(?:请|须|需|应|自行|必须|还|并|且|同时|提前|先)*"
    r"(?:上传|提交|携带|准备|下载|打印|交|带)"
)


def exclude_other_stage_materials(item: Item, source: str) -> Item:
    if item.action.review != "matched":
        return item
    current = [
        phase
        for phase in (REGISTRATION, APPLICATION)
        if phase.search(item.action.value) and phase.search(item.action.evidence)
    ]
    if len(current) != 1:
        return item

    def clearly_other_stage(material: Material) -> bool:
        if material.origin != "extracted" or material.review != "matched":
            return False
        # Check every source occurrence so a narrow quote cannot hide a valid
        # current-stage requirement elsewhere. Ignore words AI added to requirement.
        positions = list(re.finditer(re.escape(material.value), source))
        if not positions:
            return False
        for match in positions:
            position = match.start()
            boundaries = list(BOUNDARY.finditer(source, 0, position))
            left = boundaries[-1].end() if boundaries else 0
            following = BOUNDARY.search(source, position + len(material.value))
            right = following.end() if following else len(source)
            context = source[left:right]
            before_material = source[left:position]
            if current[0].search(context) or not OTHER_STAGE_REQUIREMENT.search(before_material):
                return False
        return True

    return item.model_copy(
        update={"materials": tuple(m for m in item.materials if not clearly_other_stage(m))}
    )


def retain_explicit_shared_subject(item: Item) -> Item:
    """Copy a literal universal subject for simple, single-sentence requests only.

    Conditional or unfamiliar clauses abstain; this is not a default audience.
    """

    def retain(material: Material) -> Material:
        if (
            material.origin != "extracted"
            or material.review != "matched"
            or not material.requirement.strip()
        ):
            return material
        match = SHARED_SUBJECT.match(material.evidence)
        sentence = material.evidence.rstrip(' \t”’"）)】》。！？!?；;\r\n')
        if (
            not match
            or match["subject"] in material.requirement
            or PARTICULAR_SCOPE.search(material.evidence)
            or PARTICULAR_SCOPE.search(material.requirement)
            or BOUNDARY.search(sentence)
            or not PREDICATE_START.match(material.requirement.lstrip())
            or any(
                not PREDICATE_START.match(clause.lstrip())
                for clause in re.split(r"[，,]", sentence)[1:]
            )
        ):
            return material
        return material.model_copy(
            update={
                "requirement": f"{match['subject']}：{material.requirement}",
            }
        )

    return item.model_copy(update={"materials": tuple(retain(m) for m in item.materials)})
