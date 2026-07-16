from __future__ import annotations

from dataclasses import dataclass, field

from cli_tool.devices.base import DeviceDriver


@dataclass
class DeviceRegistry:
    _drivers: dict[tuple[str, str], DeviceDriver] = field(default_factory=dict)
    _family_defaults: dict[str, DeviceDriver] = field(default_factory=dict)

    def register(self, driver: DeviceDriver, *, default_for_family: bool = False) -> None:
        key = _driver_key(driver.family, driver.model)
        if key in self._drivers:
            raise ValueError(f"driver already registered for {driver.family}/{driver.model}")
        self._drivers[key] = driver
        if default_for_family:
            self._family_defaults[_normalize(driver.family)] = driver

    def resolve(self, family: str, model: str | None = None) -> DeviceDriver:
        normalized_family = _normalize(family)
        if model:
            driver = self._drivers.get((normalized_family, _normalize(model)))
            if driver is not None:
                return driver
        driver = self._family_defaults.get(normalized_family)
        if driver is not None:
            return driver
        raise KeyError(f"no driver registered for family={family!r}, model={model!r}")


def _driver_key(family: str, model: str) -> tuple[str, str]:
    return (_normalize(family), _normalize(model))


def _normalize(value: str) -> str:
    return value.strip().casefold()

