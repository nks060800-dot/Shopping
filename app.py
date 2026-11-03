import html
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

DATA_FILE = Path(__file__).parent / "shopping_list.json"


class StorageError(Exception):
    """저장 파일을 읽거나 쓸 수 없을 때. 이 경우 파일을 절대 덮어쓰지 않는다."""


def notify(text: str, item_id: str | None = None) -> None:
    """다음 화면 그리기에서 한 번 보여줄 경고. item_id가 있으면 그 아이템 줄에 표시."""
    st.session_state["message"] = {"text": text, "item_id": item_id}


# --- 저장소 ---
# 파일이 유일한 기준 데이터다. 매 실행과 매 변경마다 파일을 새로 읽어서,
# 탭이 여러 개여도 서로의 변경을 덮어쓰지 않는다.
def _normalize(raw, index: int, used_ids: set[str]) -> dict | None:
    """파일에서 읽은 항목을 PRD 4절 구조로 맞춘다. 이름이 없으면 버린다.

    id가 없거나 중복이면 위치 기반의 결정적 id를 붙인다(실행할 때마다 같은 값이어야
    위젯 key가 유지된다). 다음 저장 때 파일에 그대로 기록된다.
    """
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name", "")).strip()
    if not name:
        return None
    try:
        quantity = max(1, int(raw.get("quantity", 1)))
    except (TypeError, ValueError):
        quantity = 1
    checked = raw.get("checked")
    checked = checked is True or (isinstance(checked, str) and checked.strip().lower() == "true")

    item_id = str(raw.get("id") or "").strip() or f"item{index}"
    base, n = item_id, 1
    while item_id in used_ids:
        item_id, n = f"{base}-{n}", n + 1
    used_ids.add(item_id)

    return {
        "id": item_id,
        "name": name,
        "quantity": quantity,
        "checked": checked,
        "created_at": str(raw.get("created_at") or ""),
    }


def _backup_corrupt_file() -> Path:
    """읽을 수 없는 파일을 지우지 않고 옆에 백업해 둔다."""
    backup = DATA_FILE.with_name(f"{DATA_FILE.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    try:
        DATA_FILE.replace(backup)
    except OSError as e:
        raise StorageError("손상된 저장 파일을 백업하지 못했습니다.") from e
    return backup


def load_items() -> list[dict]:
    """JSON 파일에서 아이템 목록을 읽는다.

    파일이 없으면 빈 목록. 내용이 손상됐으면 백업 후 빈 목록.
    파일 자체를 읽을 수 없으면(잠김 등) StorageError.
    """
    if not DATA_FILE.exists():
        return []
    try:
        raw_bytes = DATA_FILE.read_bytes()
    except OSError as e:
        raise StorageError("저장 파일을 읽을 수 없습니다.") from e
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(data, list):
            raise ValueError("not a list")
    except (UnicodeDecodeError, ValueError):  # JSONDecodeError는 ValueError의 하위 클래스
        backup = _backup_corrupt_file()
        notify(f"저장 파일이 손상되어 빈 목록으로 시작합니다. 원래 파일은 {backup.name}에 백업했습니다.")
        return []
    used_ids: set[str] = set()
    return [it for i, raw in enumerate(data) if (it := _normalize(raw, i, used_ids))]


def save_items(items: list[dict]) -> None:
    """임시 파일에 쓴 뒤 한 번에 교체한다. 쓰는 도중 꺼져도 기존 파일은 온전하다."""
    tmp = DATA_FILE.with_name(DATA_FILE.name + ".tmp")
    try:
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, DATA_FILE)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise StorageError("저장 파일에 쓸 수 없습니다.") from e


# --- 검증 ---
def validate(name: str, quantity, item_id: str | None = None) -> tuple[str, int] | None:
    """이름은 strip 후 필수, 수량은 1 이상 정수. 통과하면 (이름, 수량), 실패하면 None."""
    name = (name or "").strip()
    if not name:
        notify("아이템 이름을 입력해 주세요.", item_id)
        return None
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        quantity = 0
    if quantity < 1:
        notify("수량은 1 이상이어야 합니다.", item_id)
        return None
    return name, quantity


# --- 변경 함수 ---
def _mutate(change) -> bool:
    """파일을 새로 읽어 change(items)를 적용하고 저장한다.

    저장에 성공했을 때만 session_state를 바꾼다. 실패하면 경고를 남기고 False.
    """
    try:
        items = change(load_items())
        save_items(items)
    except StorageError as e:
        notify(f"{e} 변경 내용이 저장되지 않았습니다. "
               "(파일이 다른 프로그램에서 열려 있거나 읽기 전용인지 확인해 주세요)")
        return False
    st.session_state["items"] = items
    return True


def add_item(name: str, quantity: int) -> bool:
    item = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "quantity": quantity,
        "checked": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    return _mutate(lambda items: items + [item])


def update_item(item_id: str, name: str, quantity: int) -> bool:
    return _mutate(lambda items: [
        {**it, "name": name, "quantity": quantity} if it["id"] == item_id else it
        for it in items
    ])


def delete_item(item_id: str) -> bool:
    if st.session_state["editing_id"] == item_id:
        st.session_state["editing_id"] = None
    return _mutate(lambda items: [it for it in items if it["id"] != item_id])


def toggle_item(item_id: str, checked: bool | None = None) -> bool:
    """checked를 주면 그 값으로 설정(다른 탭에서 이미 바뀌었어도 사용자가 본 대로),
    없으면 반전한다."""
    return _mutate(lambda items: [
        {**it, "checked": (not it["checked"]) if checked is None else checked}
        if it["id"] == item_id else it
        for it in items
    ])


def clear_checked() -> int | None:
    """체크된 아이템을 모두 지우고 지운 개수를 돌려준다. 저장 실패 시 None."""
    removed = 0

    def change(items):
        nonlocal removed
        remaining = [it for it in items if not it["checked"]]
        removed = len(items) - len(remaining)
        return remaining

    if not _mutate(change):
        return None
    if st.session_state["editing_id"] not in {it["id"] for it in st.session_state["items"]}:
        st.session_state["editing_id"] = None
    return removed


# --- 위젯 콜백 ---
def on_add() -> None:
    result = validate(st.session_state["new_name"], st.session_state["new_qty"])
    if result and add_item(*result):
        st.session_state["toast"] = f"🛒 {result[0]} 추가됨"
        # 저장에 성공했을 때만 입력값을 비운다(실패 시 다시 시도할 수 있게 남겨 둠).
        st.session_state["new_name"] = ""
        st.session_state["new_qty"] = 1


def on_start_edit(item: dict) -> None:
    st.session_state["editing_id"] = item["id"]
    st.session_state[f"edit_name_{item['id']}"] = item["name"]
    st.session_state[f"edit_qty_{item['id']}"] = item["quantity"]


def on_save_edit(item_id: str) -> None:
    result = validate(
        st.session_state[f"edit_name_{item_id}"],
        st.session_state[f"edit_qty_{item_id}"],
        item_id,
    )
    if result and update_item(item_id, *result):
        st.session_state["editing_id"] = None


def on_cancel_edit() -> None:
    st.session_state["editing_id"] = None


def on_check(item_id: str) -> None:
    toggle_item(item_id, st.session_state[f"check_{item_id}"])


def on_delete(item: dict) -> None:
    if delete_item(item["id"]):
        st.session_state["toast"] = f"🗑️ {item['name']} 삭제됨"


def on_clear_checked() -> None:
    removed = clear_checked()
    if removed:
        st.session_state["toast"] = f"🧹 체크된 항목 {removed}개 삭제됨"


# --- 화면 ---
# set_page_config는 다른 st 호출(load_items의 경고 포함)보다 먼저 와야 한다.
st.set_page_config(page_title="쇼핑리스트", page_icon="🛒")

# 좁은 화면에서도 한 행의 칸들이 세로로 쪼개지지 않게 한다.
# 색은 고정값 대신 opacity로 흐리게 해서 라이트/다크 테마 모두에서 읽히게 한다.
st.markdown(
    """
    <style>
    div[data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.5rem; }
    div[data-testid="stColumn"] { min-width: 0 !important; }
    .item-name { font-size: 1.05rem; overflow-wrap: anywhere; }
    .item-name.done { text-decoration: line-through; opacity: 0.45; }
    .item-qty { font-size: 0.85rem; opacity: 0.6; white-space: nowrap; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🛒 쇼핑리스트")

# 매 실행마다 파일을 기준으로 다시 읽는다(다른 탭의 변경 반영).
try:
    st.session_state["items"] = load_items()
except StorageError as e:
    st.session_state.setdefault("items", [])
    notify(f"{e} 마지막으로 불러온 목록을 보여줍니다.")
if "editing_id" not in st.session_state:
    st.session_state["editing_id"] = None
if "new_qty" not in st.session_state:
    st.session_state["new_qty"] = 1  # 위젯 value= 대신 여기서 기본값 지정 (콜백 초기화와 충돌 방지)

# 콜백에서 남긴 알림은 한 번만 보여준다.
if toast := st.session_state.pop("toast", None):
    st.toast(toast)

# F-01 추가: [이름] [수량] [추가] 한 줄
with st.form("add_form", clear_on_submit=True, border=False):
    c_name, c_qty, c_add = st.columns([5, 1.5, 1.5], vertical_alignment="bottom")
    c_name.text_input(
        "아이템 이름", key="new_name", placeholder="예: 우유", label_visibility="collapsed",
    )
    c_qty.number_input(
        "수량", min_value=1, step=1, key="new_qty", label_visibility="collapsed",
    )
    c_add.form_submit_button("추가", on_click=on_add, type="primary", width="stretch")

message = st.session_state.pop("message", None)
items = st.session_state["items"]

# 수정 중이던 아이템이 (다른 탭 등에서) 사라졌으면 수정 모드를 해제한다.
if st.session_state["editing_id"] not in {it["id"] for it in items}:
    st.session_state["editing_id"] = None
editing_id = st.session_state["editing_id"]

# 수정 중인 아이템에 대한 경고는 그 줄에, 나머지는 입력창 아래에 보여준다.
row_message = message if message and editing_id and message["item_id"] == editing_id else None
if message and not row_message:
    st.warning(message["text"])

if not items:
    st.info("쇼핑리스트가 비어 있습니다. 위에서 아이템을 추가해 보세요.")
else:
    # F-05 진행 현황
    done = sum(it["checked"] for it in items)
    st.progress(done / len(items), text=f"**{done} / {len(items)} 완료**")
    if done == len(items):
        st.success("🎉 장보기 완료!")

    # 미완료 → 완료 순, 각 그룹 안에서는 추가한 순서 (sorted는 안정 정렬)
    for item in sorted(items, key=lambda it: it["checked"]):
        item_id = item["id"]
        with st.container(border=True):
            if item_id == editing_id:
                # form으로 묶어 Enter 키로도 저장되게 한다.
                with st.form(f"edit_form_{item_id}", border=False):
                    c_name, c_qty, c_save, c_cancel = st.columns(
                        [4, 1.6, 1, 1], vertical_alignment="center",
                    )
                    c_name.text_input(
                        "이름", key=f"edit_name_{item_id}", label_visibility="collapsed",
                    )
                    c_qty.number_input(
                        "수량", min_value=1, step=1, key=f"edit_qty_{item_id}",
                        label_visibility="collapsed",
                    )
                    # 좁은 화면에서 글자가 두 줄로 쪼개지지 않게 아이콘 + 툴팁으로 표시
                    c_save.form_submit_button(
                        ":material/check:", key=f"save_{item_id}", help="저장",
                        on_click=on_save_edit, args=(item_id,), type="primary", width="stretch",
                    )
                    c_cancel.form_submit_button(
                        ":material/close:", key=f"cancel_{item_id}", help="취소",
                        on_click=on_cancel_edit, width="stretch",
                    )
                if row_message:
                    st.warning(row_message["text"])
            else:
                c_check, c_name, c_qty, c_edit, c_del = st.columns(
                    [0.6, 5, 1, 0.9, 0.9], vertical_alignment="center",
                )
                # 위젯은 key로 자기 상태를 기억해 value=를 무시하므로, 그리기 직전에
                # 파일 기준 값으로 맞춘다(다른 탭 변경·저장 실패 시 화면 불일치 방지).
                st.session_state[f"check_{item_id}"] = item["checked"]
                c_check.checkbox(
                    "완료", key=f"check_{item_id}",
                    on_change=on_check, args=(item_id,), label_visibility="collapsed",
                )
                done_cls = " done" if item["checked"] else ""
                c_name.markdown(
                    f'<span class="item-name{done_cls}">{html.escape(item["name"])}</span>',
                    unsafe_allow_html=True,
                )
                c_qty.markdown(
                    f'<span class="item-qty">x{item["quantity"]}</span>',
                    unsafe_allow_html=True,
                )
                c_edit.button(
                    "✏️", key=f"edit_{item_id}", help="수정", on_click=on_start_edit,
                    args=(item,), type="tertiary",
                    disabled=editing_id is not None,  # 한 번에 한 아이템만 수정
                )
                c_del.button(
                    "🗑️", key=f"del_{item_id}", help="삭제", on_click=on_delete,
                    args=(item,), type="tertiary",
                )

    # F-03 체크된 항목 모두 삭제 (가운데 정렬)
    _, c_clear, _ = st.columns([1, 2, 1])
    c_clear.button(
        "체크된 항목 모두 삭제", key="clear_checked", on_click=on_clear_checked,
        disabled=done == 0, width="stretch",
    )
