from skytrap.ui.terminal import ChatState, make_mode_aware_confirm


def test_chat_state_starts_normal():
    assert ChatState().mode == "normal"


def test_cycle_mode_goes_normal_plan_auto_normal():
    state = ChatState()
    assert state.mode == "normal"
    state.cycle_mode()
    assert state.mode == "plan"
    state.cycle_mode()
    assert state.mode == "auto"
    state.cycle_mode()
    assert state.mode == "normal"


def test_mode_aware_confirm_defers_to_base_when_not_auto():
    state = ChatState()
    calls = []

    def base_confirm(preview: str) -> bool:
        calls.append(preview)
        return False

    wrapped = make_mode_aware_confirm(base_confirm, state)

    assert wrapped("do the thing") is False
    assert calls == ["do the thing"]


def test_mode_aware_confirm_auto_approves_without_calling_base():
    state = ChatState()
    state.mode = "auto"
    calls = []

    def base_confirm(preview: str) -> bool:
        calls.append(preview)
        return False  # would decline if it were ever called

    wrapped = make_mode_aware_confirm(base_confirm, state)

    assert wrapped("do the thing") is True
    assert calls == []  # base confirm must never be consulted in auto mode
