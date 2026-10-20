from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np


@dataclass
class BandChannel:
    name: str
    x: np.ndarray
    energies: np.ndarray  # shape (nk, nb)
    segments: list[tuple[int, int]]
    ticks: np.ndarray
    labels: list[str]
    occupations: np.ndarray | None = None
    weights: np.ndarray | None = None
    kpoints: np.ndarray | None = None
    metadata: dict = field(default_factory=dict)

    def shifted(self, reference: float) -> "BandChannel":
        return BandChannel(
            name=self.name,
            x=self.x.copy(),
            energies=self.energies - reference,
            segments=list(self.segments),
            ticks=self.ticks.copy(),
            labels=list(self.labels),
            occupations=None if self.occupations is None else self.occupations.copy(),
            weights=None if self.weights is None else self.weights.copy(),
            kpoints=None if self.kpoints is None else self.kpoints.copy(),
            metadata={**self.metadata, "energy_reference_eV": reference},
        )


@dataclass
class DOSCurve:
    label: str
    energy: np.ndarray
    values: np.ndarray
    spin: str | None = None


@dataclass
class DOSData:
    curves: list[DOSCurve]
    fermi_eV: float | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ProcarData:
    kpoints: np.ndarray
    kweights: np.ndarray
    energies_by_spin: dict[int, np.ndarray]  # spin -> (nk, nb)
    projections_by_spin: dict[int, np.ndarray]  # spin -> (nk, nb, nion, norb)
    orbital_labels: list[str]
    metadata: dict = field(default_factory=dict)
