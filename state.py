"""Session state tracker — turns the packet stream into discrete events.

Watches the player car's Lap Data for sector transitions and lap
completions, and keeps live tyre/fuel context from Car Status.
Sector 3 time is never sent directly mid-stream; it is derived on lap
completion as last_lap_time - s1 - s2.
"""
from typing import NamedTuple

from packets import CarStatus, CarTelemetry, LapData, PacketHeader


class TelemetryStats(NamedTuple):
    """Aggregate driving stats for one sector."""
    top_speed: int          # kph
    full_throttle_pct: int  # % of samples at >=96% throttle
    brake_count: int        # distinct brake applications
    samples: int


class _TelAccum:
    """Accumulates Car Telemetry samples for the sector in progress."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.samples = 0
        self.top_speed = 0
        self.full_throttle = 0
        self.brakes = 0
        self._braking = False

    def add(self, tel: CarTelemetry):
        self.samples += 1
        self.top_speed = max(self.top_speed, tel.speed_kph)
        if tel.throttle >= 0.96:
            self.full_throttle += 1
        if tel.brake >= 0.4 and not self._braking:
            self.brakes += 1
            self._braking = True
        elif tel.brake < 0.2:
            self._braking = False

    def snapshot(self) -> TelemetryStats:
        pct = round(100 * self.full_throttle / self.samples) if self.samples else 0
        return TelemetryStats(self.top_speed, pct, self.brakes, self.samples)


class SectorComplete(NamedTuple):
    lap_num: int
    sector: int          # 1, 2 or 3
    time_ms: int
    lap_invalid: bool
    stats: TelemetryStats | None = None


class LapComplete(NamedTuple):
    lap_num: int
    lap_time_ms: int
    sector_times_ms: tuple  # (s1, s2, s3)
    invalid: bool


class SessionState:
    def __init__(self):
        self._reset_session()

    def _reset_session(self):
        self.session_uid = None
        self.lap_num = None
        self.sector = None
        self.s1_ms = None
        self.s2_ms = None
        self.lap_invalid = False
        self.speed_trap_kph = 0.0
        self.car_position = None
        self.gap_ahead_ms = None
        self._tel = _TelAccum()
        # Live context from Car Status
        self.tyre_age_laps = None
        self.visual_tyre_compound = None
        self.fuel_in_tank = None
        self.fuel_remaining_laps = None

    def _resync(self, lap: LapData):
        """Adopt the car's current lap/sector without emitting events —
        used on first packet, flashbacks, and restarts."""
        self.lap_num = lap.current_lap_num
        self.sector = lap.sector
        self.s1_ms = lap.sector1_ms or None
        self.s2_ms = lap.sector2_ms or None
        self.lap_invalid = bool(lap.current_lap_invalid)
        self._tel.reset()

    def update_from_lap_data(self, header: PacketHeader, lap: LapData) -> list:
        events = []

        if header.session_uid != self.session_uid:
            self._reset_session()
            self.session_uid = header.session_uid
            self._resync(lap)
            return events

        self.speed_trap_kph = max(self.speed_trap_kph, lap.speed_trap_fastest_speed)
        self.car_position = lap.car_position or None
        self.gap_ahead_ms = (lap.delta_front_ms_part
                             + 60_000 * lap.delta_front_min_part) or None

        # In the garage or in the pits: keep state pinned, emit nothing.
        if lap.driver_status == 0 or lap.pit_status != 0:
            self._resync(lap)
            return events

        if self.lap_num is None:
            self._resync(lap)
            return events

        if lap.current_lap_num == self.lap_num:
            self.lap_invalid = self.lap_invalid or bool(lap.current_lap_invalid)
            if lap.sector != self.sector:
                if self.sector == 0 and lap.sector == 1 and lap.sector1_ms > 0:
                    self.s1_ms = lap.sector1_ms
                    events.append(SectorComplete(self.lap_num, 1, self.s1_ms,
                                                 self.lap_invalid, self._tel.snapshot()))
                elif self.sector == 1 and lap.sector == 2 and lap.sector2_ms > 0:
                    self.s2_ms = lap.sector2_ms
                    events.append(SectorComplete(self.lap_num, 2, self.s2_ms,
                                                 self.lap_invalid, self._tel.snapshot()))
                else:
                    self._resync(lap)  # flashback rewound the sector
                    return events
                self._tel.reset()
                self.sector = lap.sector

        elif lap.current_lap_num == self.lap_num + 1:
            # Crossed the line: previous lap is complete.
            last = lap.last_lap_time_ms
            if last > 0 and self.s1_ms and self.s2_ms:
                s3 = last - self.s1_ms - self.s2_ms
                if s3 > 0:
                    events.append(SectorComplete(self.lap_num, 3, s3, self.lap_invalid,
                                                 self._tel.snapshot()))
                    events.append(LapComplete(
                        self.lap_num, last, (self.s1_ms, self.s2_ms, s3), self.lap_invalid))
            self._resync(lap)
        else:
            # Lap number jumped or went backwards (restart/flashback).
            self._resync(lap)

        return events

    def update_from_telemetry(self, tel: CarTelemetry) -> None:
        self._tel.add(tel)

    def update_from_car_status(self, status: CarStatus) -> None:
        self.tyre_age_laps = status.tyres_age_laps
        self.visual_tyre_compound = status.visual_tyre_compound
        self.fuel_in_tank = status.fuel_in_tank
        self.fuel_remaining_laps = status.fuel_remaining_laps
