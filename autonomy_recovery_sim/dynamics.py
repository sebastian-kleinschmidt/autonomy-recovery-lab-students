from __future__ import annotations

import math
from dataclasses import dataclass


MAX_REVERSE_SPEED_MPS = 2.0


def clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def wrap_angle(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


@dataclass(frozen=True)
class Control:
    throttle: float = 0.0
    brake: float = 0.0
    steering: float = 0.0
    reverse: bool = False

    def clamped(self) -> "Control":
        return Control(
            throttle=clamp(self.throttle, 0.0, 1.0),
            brake=clamp(self.brake, 0.0, 1.0),
            steering=clamp(self.steering, -1.0, 1.0),
            reverse=self.reverse,
        )


@dataclass
class Vehicle:
    x_m: float
    y_m: float
    yaw_rad: float
    speed_mps: float
    length_m: float = 4.5
    width_m: float = 1.8
    wheelbase_m: float = 2.7


def step_vehicle(vehicle: Vehicle, control: Control, dt_s: float) -> None:
    """Einfaches kinematisches Fahrradmodell mit begrenzter Lenkung."""
    command = control.clamped()
    if command.reverse:
        # Rueckwaertsgang: negative Geschwindigkeit, gedrosselt auf Kriechtempo.
        acceleration = -command.throttle * 1.5 + command.brake * 6.0 - vehicle.speed_mps * 0.045
        vehicle.speed_mps = clamp(vehicle.speed_mps + acceleration * dt_s, -MAX_REVERSE_SPEED_MPS, 0.0)
    else:
        acceleration = command.throttle * 2.4 - command.brake * 6.0 - vehicle.speed_mps * 0.045
        vehicle.speed_mps = clamp(vehicle.speed_mps + acceleration * dt_s, 0.0, 16.0)
    steering_angle = math.radians(31.0) * command.steering
    vehicle.yaw_rad = wrap_angle(
        vehicle.yaw_rad + vehicle.speed_mps / vehicle.wheelbase_m * math.tan(steering_angle) * dt_s
    )
    vehicle.x_m += math.cos(vehicle.yaw_rad) * vehicle.speed_mps * dt_s
    vehicle.y_m += math.sin(vehicle.yaw_rad) * vehicle.speed_mps * dt_s


def oriented_corners(vehicle: Vehicle) -> tuple[tuple[float, float], ...]:
    half_l, half_w = vehicle.length_m / 2.0, vehicle.width_m / 2.0
    cos_yaw, sin_yaw = math.cos(vehicle.yaw_rad), math.sin(vehicle.yaw_rad)
    corners = []
    for longitudinal, lateral in ((half_l, half_w), (half_l, -half_w), (-half_l, -half_w), (-half_l, half_w)):
        corners.append((
            vehicle.x_m + longitudinal * cos_yaw - lateral * sin_yaw,
            vehicle.y_m + longitudinal * sin_yaw + lateral * cos_yaw,
        ))
    return tuple(corners)


def boxes_overlap(first: Vehicle, second: Vehicle) -> bool:
    a, b = oriented_corners(first), oriented_corners(second)
    for polygon in (a, b):
        for start, end in zip(polygon, polygon[1:] + polygon[:1]):
            axis = (-(end[1] - start[1]), end[0] - start[0])
            a_projection = [point[0] * axis[0] + point[1] * axis[1] for point in a]
            b_projection = [point[0] * axis[0] + point[1] * axis[1] for point in b]
            if max(a_projection) < min(b_projection) or max(b_projection) < min(a_projection):
                return False
    return True
