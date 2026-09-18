from __future__ import annotations

from app.models.need import CommunityNeed
from app.models.task_workflow import Task, TaskAssignment
from app.services.line_notify import push_flex_message, reply_flex_message
from app.services.task_line_security import TaskLineCommand, build_task_postback_data


COMMANDS_BY_STATUS = {
    "ASSIGNED": [TaskLineCommand.ACCEPT, TaskLineCommand.DECLINE],
    "ACKNOWLEDGED": [TaskLineCommand.START, TaskLineCommand.FAIL],
    "IN_PROGRESS": [TaskLineCommand.ARRIVE, TaskLineCommand.FAIL],
    "ARRIVED": [TaskLineCommand.COMPLETE, TaskLineCommand.FAIL],
}

COMMAND_LABELS = {
    TaskLineCommand.ACCEPT: "接受任務",
    TaskLineCommand.START: "開始執行",
    TaskLineCommand.ARRIVE: "已抵達",
    TaskLineCommand.COMPLETE: "完成任務",
    TaskLineCommand.DECLINE: "拒絕任務",
    TaskLineCommand.FAIL: "回報失敗",
}

STATUS_LABELS = {
    "ASSIGNED": "等待接受",
    "ACKNOWLEDGED": "已接受",
    "IN_PROGRESS": "執行中",
    "ARRIVED": "已抵達",
    "COMPLETED": "已完成",
    "FAILED": "執行失敗",
    "BLOCKED": "等待重新指派",
    "CANCELLED": "已取消",
}


def _button(
    command: TaskLineCommand,
    task: Task,
    assignment: TaskAssignment,
) -> dict:
    primary = command in {
        TaskLineCommand.ACCEPT,
        TaskLineCommand.START,
        TaskLineCommand.ARRIVE,
        TaskLineCommand.COMPLETE,
    }
    button = {
        "type": "button",
        "style": "primary" if primary else "secondary",
        "height": "sm",
        "action": {
            "type": "postback",
            "label": COMMAND_LABELS[command],
            "displayText": COMMAND_LABELS[command],
            "data": build_task_postback_data(
                command=command,
                task_id=str(task.id),
                assignment_id=str(assignment.id),
                expected_version=task.version,
                assignee_id=str(assignment.assignee_id),
            ),
        },
    }
    if primary:
        button["color"] = "#147D74"
    return button


def build_task_assignment_flex(
    task: Task,
    assignment: TaskAssignment,
    need: CommunityNeed,
) -> dict:
    """Build a privacy-minimized task card for the assigned volunteer."""
    contents = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#147D74",
            "contents": [
                {
                    "type": "text",
                    "text": "志工執行任務",
                    "color": "#FFFFFF",
                    "size": "md",
                    "weight": "bold",
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"狀態：{STATUS_LABELS.get(task.status, task.status)}",
                    "size": "sm",
                    "weight": "bold",
                    "color": "#147D74",
                },
                {
                    "type": "text",
                    "text": f"Task ID：{task.id}",
                    "size": "xs",
                    "color": "#666666",
                    "wrap": True,
                },
                {
                    "type": "text",
                    "text": f"任務內容：{need.description or need.need_type}",
                    "size": "sm",
                    "wrap": True,
                },
                {
                    "type": "text",
                    "text": f"地點：{need.address or '請聯絡管理員確認'}",
                    "size": "sm",
                    "wrap": True,
                },
                {
                    "type": "text",
                    "text": (
                        "必要注意事項："
                        f"{task.instructions or '請先確認路線與自身安全，異常時立即回報。'}"
                    ),
                    "size": "sm",
                    "color": "#9A5A00",
                    "wrap": True,
                },
            ],
        },
    }
    commands = COMMANDS_BY_STATUS.get(task.status, [])
    if commands:
        contents["footer"] = {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [_button(command, task, assignment) for command in commands],
        }
    return contents


def send_task_assignment_message(
    line_uid: str,
    task: Task,
    assignment: TaskAssignment,
    need: CommunityNeed,
) -> None:
    push_flex_message(
        line_uid,
        "志工執行任務",
        build_task_assignment_flex(task, assignment, need),
    )


def reply_task_progress_message(
    reply_token: str,
    task: Task,
    assignment: TaskAssignment,
    need: CommunityNeed,
) -> None:
    reply_flex_message(
        reply_token,
        f"任務狀態：{STATUS_LABELS.get(task.status, task.status)}",
        build_task_assignment_flex(task, assignment, need),
    )
