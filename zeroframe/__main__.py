"""Deterministic demonstration with synthetic observations only."""

from .state import Observation, SeatStateMachine, Status


def main() -> None:
    machine = SeatStateMachine()
    print("Synthetic demo: no camera, model, or network is used.")
    print("empty_verified below is a test signal, not a model result.")
    print(f" 0s: {machine.state.status.value}")
    seen = {machine.state.status}
    for second in range(1, 15):
        state = machine.observe(
            Observation(
                observed_at=second,
                person=second <= 3,
                belongings=4 <= second <= 8,
                empty_verified=second == 14,
            )
        )
        seen.add(state.status)
        print(f"{second:2}s: {state.status.value}")
    state = machine.tick(44)
    print(f"44s: {state.status.value} ({state.reason.value})")
    assert seen == set(Status) and state.status == Status.UNKNOWN


if __name__ == "__main__":
    main()
