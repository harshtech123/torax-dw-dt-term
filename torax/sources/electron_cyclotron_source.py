from __future__ import annotations

import dataclasses

import chex
import jax
import jax.numpy as jnp

from torax import array_typing
from torax import geometry
from torax import interpolated_param
from torax import state
from torax.config import runtime_params_slice
from torax.constants import CONSTANTS
from torax.sources import runtime_params as runtime_params_lib
from torax.sources import source


@dataclasses.dataclass(kw_only=True)
class RuntimeParams(runtime_params_lib.RuntimeParams):
    """Runtime parameters for the electron-cyclotron source."""

    # Global dimensionless current drive efficiency
    global_efficiency: runtime_params_lib.interpolated_param.TimeInterpolated

    # EC power density profile on the rho grid; units [W/m^3]
    # TODO: Create a interpolated_param.TimeRhoInterpolated that can handle
    # interpolation modes in both rho and time
    ec_power_density: runtime_params_lib.interpolated_param.InterpolatedVarTimeRhoInput

    def make_provider(self, torax_mesh: geometry.Grid1D | None = None):
        if torax_mesh is None:
            raise ValueError("torax_mesh is required for RuntimeParams.make_provider.")
        return RuntimeParamsProvider(**self.get_provider_kwargs(torax_mesh))


@chex.dataclass
class RuntimeParamsProvider(runtime_params_lib.RuntimeParamsProvider):
    """Provides runtime parameters for the electron-cyclotron source for a given time and geometry."""

    runtime_params_config: RuntimeParams
    global_efficiency: interpolated_param.InterpolatedVarSingleAxis
    ec_power_density: interpolated_param.InterpolatedVarTimeRho

    def build_dynamic_params(
        self,
        t: chex.Numeric,
    ) -> DynamicRuntimeParams:
        return DynamicRuntimeParams(**self.get_dynamic_params_kwargs(t))


@chex.dataclass(frozen=True)
class DynamicRuntimeParams(runtime_params_lib.DynamicRuntimeParams):
    """Runtime parameters for the electron-cyclotron source for a given time and geometry."""

    global_efficiency: array_typing.ScalarFloat
    ec_power_density: array_typing.ArrayFloat


def _calc_heating_and_linliu_current(
    static_runtime_params_slice: runtime_params_slice.StaticRuntimeParamsSlice,
    dynamic_runtime_params_slice: runtime_params_slice.DynamicRuntimeParamsSlice,
    dynamic_source_runtime_params: runtime_params_lib.DynamicRuntimeParams,
    geo: geometry.Geometry,
    core_profiles: state.CoreProfiles,
) -> jax.Array:
    """Model function for the electron-cyclotron source.

    Returns:
      (ec_power_density, j_parallel_ec)
    """
    assert isinstance(dynamic_source_runtime_params, DynamicRuntimeParams)

    # Sources:
    # Original equation for local efficiency from
    # - Electron cyclotron current drive efficiency in general tokamak geometry
    #   Lin-Liu, Chan, and Prater, 2003
    #   https://doi.org/10.1063/1.1610472
    # Conversion to global dimensionless efficiency from
    # - Flat-top plasma operational space of the STEP power plant
    #   Tholerus et al., 2024
    #   https://doi.org/10.1088/1741-4326/ad6ea2
    #
    # Note an additional B0 term appears, as TORAX expects <j.B> rather than <j.B>/B0
    #
    # Units:
    # - epsilon0^2: [m^-6 kg^-2 s^8 A^4]
    # - qe^-3: [C^-3] = [A^-3 s^-3]
    # - global_efficiency: [dimensionless]
    # - ec_power_density: [W/m^3] = [kg m^-1 s^-3]
    # - Te: [J] = [kg m^2 s^-2]
    # - ne^-1: [m^3]

    ne_face_in_m3 = (
        core_profiles.ne.face_value() * dynamic_runtime_params_slice.numerics.nref
    )
    Te_face_in_J = core_profiles.temp_el.face_value() * CONSTANTS.keV2J

    total_ec_power = jax.scipy.integrate.trapezoid(
        dynamic_source_runtime_params.ec_power_density * geo.vpr_face, geo.rho_face_norm
    )
    weighted_ec_power = jax.scipy.integrate.trapezoid(
        dynamic_source_runtime_params.ec_power_density
        * Te_face_in_J
        / ne_face_in_m3
        * geo.vpr_face,
        geo.rho_face_norm,
    )
    local_efficiency = weighted_ec_power / total_ec_power
    j_ec_parallel = (
        dynamic_source_runtime_params.global_efficiency
        * 2
        * jnp.pi
        * CONSTANTS.epsilon0**2
        / CONSTANTS.qe**3
        * local_efficiency
        * dynamic_source_runtime_params.ec_power_density
    )

    return jnp.stack(
        [dynamic_source_runtime_params.ec_power_density, j_ec_parallel * geo.B0]
    )


def _get_ec_output_shape(geo: geometry.Geometry) -> tuple[int, ...]:
    return (2,) + source.ProfileType.CELL.get_profile_shape(geo)


@dataclasses.dataclass(kw_only=True, frozen=True, eq=True)
class ElectronCyclotronSource(source.Source):
    """Electron cyclotron source for the Psi and Te equations."""

    affected_core_profiles: tuple[source.AffectedCoreProfile, ...] = dataclasses.field(
        init=False,
        default=(
            source.AffectedCoreProfile.TEMP_EL,
            source.AffectedCoreProfile.PSI,
        ),
    )

    output_shape_getter: source.SourceOutputShapeFunction = dataclasses.field(
        init=False,
        default_factory=lambda: _get_ec_output_shape,
    )

    supported_modes: tuple[runtime_params_lib.Mode, ...] = (
        runtime_params_lib.Mode.ZERO,
        runtime_params_lib.Mode.MODEL_BASED,
    )

    model_func: source.SourceProfileFunction = _calc_heating_and_linliu_current

    def get_value(
        self,
        dynamic_runtime_params_slice: runtime_params_slice.DynamicRuntimeParamsSlice,
        dynamic_source_runtime_params: runtime_params_lib.DynamicRuntimeParams,
        geo: geometry.Geometry,
        core_profiles: state.CoreProfiles | None = None,
    ) -> jax.Array:
        """Computes the TEMP_EL and PSI values of the source.

        Args:
          dynamic_runtime_params_slice: Input config which can change from time step
            to time step.
          dynamic_source_runtime_params: Slice of this source's runtime parameters
            at a specific time t.
          geo: Geometry of the torus.
          core_profiles: Core plasma profiles used to compute the source's profiles.

        Returns:
          2 stacked arrays, the first for the TEMP_EL profile and the second for the
          PSI profile.
        """
        output_shape = self.output_shape_getter(geo)
        profile = super().get_value(
            dynamic_runtime_params_slice=dynamic_runtime_params_slice,
            dynamic_source_runtime_params=dynamic_source_runtime_params,
            geo=geo,
            core_profiles=core_profiles,
        )
        assert isinstance(profile, jax.Array)
        chex.assert_rank(profile, 2)
        chex.assert_shape(profile, output_shape)
        return profile


ElectronCyclotronSourceBuilder = source.make_source_builder(
    ElectronCyclotronSource, runtime_params_type=RuntimeParams
)
