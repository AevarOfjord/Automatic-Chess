from __future__ import annotations

import dataclasses
import math
import threading

from .config import ArmId, RobotConfig
from .geometry import BoardLayout, JointPose, Point, ScaraKinematics
from .inventory import PhysicalInventory
from .logging_config import get_logger
from .protocol import Action, ArmCommand, ArmResponse, CommandJournal, Status, new_command_id
from .trajectory import PuckTrajectoryPlanner, TrajectoryPlanningError
from .transport import GatewayTransport, MockGatewayTransport, SerialGatewayTransport

log = get_logger("hardware")


class MotionFault(RuntimeError):
    pass


class DualArmHardware:
    """Collision-serialized PC motion controller for both SCARA mechanisms."""

    def __init__(
        self,
        config: RobotConfig | None = None,
        transport: GatewayTransport | None = None,
        use_mock: bool = True,
    ) -> None:
        self.config = config or RobotConfig()
        self.layout = BoardLayout(self.config)
        self.transport = transport or (
            MockGatewayTransport()
            if use_mock
            else SerialGatewayTransport(self.config.serial_port, self.config.serial_baudrate)
        )
        self.journal = CommandJournal(self.config.journal_path)
        self.workspace_lock = threading.Lock()
        self.last_pose: dict[ArmId, JointPose | None] = {ArmId.WHITE: None, ArmId.BLACK: None}
        self.parked: dict[ArmId, bool] = {ArmId.WHITE: False, ArmId.BLACK: False}
        self.trajectory_planner = PuckTrajectoryPlanner(self.config, self.layout)

    def _send(self, command: ArmCommand) -> ArmResponse:
        attempts = 1 + max(0, self.config.command_retries)
        last_error = "no attempts executed"
        for attempt in range(attempts):
            active = (
                command
                if attempt == 0
                else dataclasses.replace(command, command_id=new_command_id())
            )
            self.journal.record("command", active)
            response = self.transport.exchange(active, self.config.response_timeout_s)
            self.journal.record("response", response)
            if response.status is Status.DONE:
                return response
            last_error = (
                f"{active.arm.value} {active.action.value} failed: "
                f"{response.detail or response.status.value}"
            )
            log.warning("command failed (attempt %s/%s): %s", attempt + 1, attempts, last_error)
            # Retry transient gateway timeouts only; permanent faults fail immediately.
            if "timeout" not in (response.detail or "").lower():
                break
        raise MotionFault(last_error)

    def _pose(self, arm: ArmId, location: str) -> JointPose:
        return self._pose_for_point(arm, self.layout.location(location), location)

    def _pose_for_point(self, arm: ArmId, point: Point, label: str = "waypoint") -> JointPose:
        result = ScaraKinematics(self.config.arm(arm)).inverse(point, self.last_pose[arm])
        if not result.reachable or result.pose is None:
            raise MotionFault(f"{arm.value} cannot reach {label}: {result.reason}")
        return result.pose

    def _trajectory(self, arm: ArmId, points: list[tuple[JointPose, float]]) -> None:
        # Compact waypoint arrays keep each command inside the conservative ESP-NOW payload limit.
        for index in range(0, len(points), 4):
            chunk = points[index : index + 4]
            payload = {"p": [pose.as_wire(z) for pose, z in chunk]}
            self._send(ArmCommand(arm, Action.EXECUTE_TRAJECTORY, payload))
            self.last_pose[arm] = chunk[-1][0]
            self.parked[arm] = False

    def _is_near_park(self, arm: ArmId) -> bool:
        if self.parked[arm]:
            return True
        pose = self.last_pose[arm]
        if pose is None:
            return False
        park_pose = self._pose(arm, f"park:{arm.value}")
        return (
            math.hypot(pose.shoulder_deg - park_pose.shoulder_deg, pose.elbow_deg - park_pose.elbow_deg)
            < 1.5
        )

    def ensure_parked(self, arm: ArmId) -> None:
        """Park an arm if it is not already near its park pose."""
        if not self._is_near_park(arm):
            self.park(arm)

    def _plan_path_points(
        self,
        arm: ArmId,
        source: str,
        destination: str,
        inventory: PhysicalInventory | None,
        token_id: str | None,
    ) -> list[Point]:
        if inventory is None or token_id is None:
            return [self.layout.location(source), self.layout.location(destination)]
        try:
            return self.trajectory_planner.plan_transfer(
                inventory, token_id, source, destination
            ).points
        except TrajectoryPlanningError as direct_error:
            # Crowded mid-game resets can leave no open corridor. Try a free
            # intermediate (arm buffers, then an empty dead slot) as a detour.
            intermediates: list[str] = []
            for candidate_arm in (arm, arm.opposite):
                buffer = f"buffer:{candidate_arm.value}"
                if buffer not in {source, destination} and inventory.token_at(buffer) is None:
                    intermediates.append(buffer)
            for candidate_arm in ArmId:
                try:
                    empty_dead = inventory.first_empty_dead_slot(candidate_arm)
                except RuntimeError:
                    continue
                if empty_dead not in {source, destination}:
                    intermediates.append(empty_dead)
            last_error: Exception = direct_error
            for mid_name in intermediates:
                try:
                    leg1 = self.trajectory_planner.plan_transfer(
                        inventory, token_id, source, mid_name
                    ).points
                    mid_state = inventory.clone()
                    mid_state.move(token_id, mid_name)
                    leg2 = self.trajectory_planner.plan_transfer(
                        mid_state, token_id, mid_name, destination
                    ).points
                    return leg1 + leg2[1:]
                except TrajectoryPlanningError as exc:
                    last_error = exc
                    continue
            raise last_error from direct_error

    def home(self, arm: ArmId) -> None:
        self._send(ArmCommand(arm, Action.HOME))
        self.last_pose[arm] = None
        self.parked[arm] = False

    def home_all(self) -> None:
        self.home(ArmId.WHITE)
        self.home(ArmId.BLACK)
        self.park_all()

    def park(self, arm: ArmId) -> None:
        cfg = self.config.arm(arm)
        pose = self._pose(arm, f"park:{arm.value}")
        self._send(ArmCommand(arm, Action.PARK, {"p": pose.as_wire(cfg.fixed_tool_z_mm)}))
        self.last_pose[arm] = pose
        self.parked[arm] = True

    def park_all(self) -> None:
        self.park(ArmId.WHITE)
        self.park(ArmId.BLACK)

    def stop_all(self) -> None:
        for arm in ArmId:
            try:
                self._send(ArmCommand(arm, Action.STOP))
            except MotionFault:
                pass
            self.parked[arm] = False

    def transfer(
        self,
        arm: ArmId,
        source: str,
        destination: str,
        *,
        inventory: PhysicalInventory | None = None,
        token_id: str | None = None,
    ) -> None:
        cfg = self.config.arm(arm)
        source_pose = self._pose(arm, source)
        destination_pose = self._pose(arm, destination)
        park_pose = self._pose(arm, f"park:{arm.value}")
        try:
            path_points = self._plan_path_points(arm, source, destination, inventory, token_id)
        except TrajectoryPlanningError as exc:
            raise MotionFault(f"{arm.value} cannot plan puck path {source}->{destination}: {exc}") from exc
        carry_poses = [
            (self._pose_for_point(arm, point, f"puck path {index}"), cfg.fixed_tool_z_mm)
            for index, point in enumerate(path_points[1:], start=1)
        ]
        with self.workspace_lock:
            # Keep-out policy: only one arm may work the table; park the opposite arm first.
            log.debug("transfer %s %s -> %s token=%s", arm.value, source, destination, token_id)
            self.ensure_parked(arm.opposite)
            self._trajectory(
                arm,
                [
                    (park_pose, cfg.fixed_tool_z_mm),
                    (source_pose, cfg.fixed_tool_z_mm),
                ],
            )
            pickup = self._send(ArmCommand(arm, Action.SET_MAGNET, {"on": True}))
            if pickup.telemetry.get("pickup") is False:
                raise MotionFault(f"{arm.value} pickup sensor did not detect a piece at {source}")
            self._trajectory(arm, carry_poses or [(destination_pose, cfg.fixed_tool_z_mm)])
            self._send(ArmCommand(arm, Action.SET_MAGNET, {"on": False}))
            self.park(arm)

    def close(self) -> None:
        self.transport.close()
