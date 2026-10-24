"""Conservative exclusion of materials explicitly used in a different stage.

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
