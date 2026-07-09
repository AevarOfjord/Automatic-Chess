from __future__ import annotations

import threading

from .config import ArmId, RobotConfig
from .geometry import BoardLayout, JointPose, Point, ScaraKinematics
from .inventory import PhysicalInventory
from .protocol import Action, ArmCommand, ArmResponse, CommandJournal, Status
from .trajectory import PuckTrajectoryPlanner, TrajectoryPlanningError
from .transport import GatewayTransport, MockGatewayTransport, SerialGatewayTransport


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
        self.trajectory_planner = PuckTrajectoryPlanner(self.config, self.layout)

    def _send(self, command: ArmCommand) -> ArmResponse:
        self.journal.record("command", command)
        response = self.transport.exchange(command, self.config.response_timeout_s)
        self.journal.record("response", response)
        if response.status is not Status.DONE:
            raise MotionFault(
                f"{command.arm.value} {command.action.value} failed: {response.detail or response.status.value}"
            )
        return response

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

    def home(self, arm: ArmId) -> None:
        self._send(ArmCommand(arm, Action.HOME))
        self.last_pose[arm] = None

    def home_all(self) -> None:
        self.home(ArmId.WHITE)
        self.home(ArmId.BLACK)
        self.park_all()

    def park(self, arm: ArmId) -> None:
        cfg = self.config.arm(arm)
        pose = self._pose(arm, f"park:{arm.value}")
        self._send(ArmCommand(arm, Action.PARK, {"p": pose.as_wire(cfg.fixed_tool_z_mm)}))
        self.last_pose[arm] = pose

    def park_all(self) -> None:
        self.park(ArmId.WHITE)
        self.park(ArmId.BLACK)

    def stop_all(self) -> None:
        for arm in ArmId:
            try:
                self._send(ArmCommand(arm, Action.STOP))
            except MotionFault:
                pass

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
            path_points = (
                self.trajectory_planner.plan_transfer(inventory, token_id, source, destination).points
                if inventory is not None and token_id is not None
                else [self.layout.location(source), self.layout.location(destination)]
            )
        except TrajectoryPlanningError as exc:
            raise MotionFault(f"{arm.value} cannot plan puck path {source}->{destination}: {exc}") from exc
        carry_poses = [
            (self._pose_for_point(arm, point, f"puck path {index}"), cfg.fixed_tool_z_mm)
            for index, point in enumerate(path_points[1:], start=1)
        ]
        with self.workspace_lock:
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
