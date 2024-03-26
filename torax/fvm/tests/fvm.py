# Copyright 2024 DeepMind Technologies Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for torax.fvm."""
import copy
import dataclasses
from typing import Callable
from absl.testing import absltest
from absl.testing import parameterized
import jax
from jax import numpy as jnp
import numpy as np
from torax import calc_coeffs
from torax import config as config_lib
from torax import config_slice
from torax import fvm
from torax import geometry
from torax import initial_states
from torax.fvm import implicit_solve_block
from torax.fvm import residual_and_loss
from torax.sources import source_config
from torax.sources import source_profiles as source_profiles_lib
from torax.tests.test_lib import torax_refs
from torax.transport_model import transport_model_factory


class FVMTest(torax_refs.ReferenceValueTest):
  """Unit tests for the `torax.fvm` module."""

  @parameterized.parameters([
      dict(references_getter=torax_refs.circular_references),
      dict(references_getter=torax_refs.chease_references_Ip_from_chease),
      dict(references_getter=torax_refs.chease_references_Ip_from_config),
  ])
  def test_face_grad(
      self,
      references_getter: Callable[[], torax_refs.References],
  ):
    """Test that CellVariable.face_grad matches reference values."""
    references = references_getter()

    face_grad_jax = references.psi.face_grad()

    np.testing.assert_allclose(face_grad_jax, references.psi_face_grad)

  @parameterized.parameters([
      dict(references_getter=torax_refs.circular_references),
      dict(references_getter=torax_refs.chease_references_Ip_from_chease),
      dict(references_getter=torax_refs.chease_references_Ip_from_config),
  ])
  def test_underconstrained(
      self,
      references_getter: Callable[[], torax_refs.References],
  ):
    """Test that CellVariable raises for underconstrained problems."""
    references = references_getter()

    # Use ref_config to configure size, so we can also use ref_geo
    value = jnp.zeros(references.config.nr)
    cell_variable = fvm.CellVariable(value=value, dr=references.geo.dr)
    # Underconstrain the left
    with self.assertRaises(AssertionError):
      dataclasses.replace(
          cell_variable,
          left_face_constraint=None,
          left_face_grad_constraint=None,
      )
    # Underconstrain the right
    with self.assertRaises(AssertionError):
      dataclasses.replace(
          cell_variable,
          right_face_constraint=None,
          right_face_grad_constraint=None,
      )

  @parameterized.parameters([
      dict(references_getter=torax_refs.circular_references),
      dict(references_getter=torax_refs.chease_references_Ip_from_chease),
      dict(references_getter=torax_refs.chease_references_Ip_from_config),
  ])
  def test_overconstrained(
      self,
      references_getter: Callable[[], torax_refs.References],
  ):
    """Test that CellVariable raises for overconstrained problems."""
    references = references_getter()

    # Use ref_config to configure size, so we can also use ref_geo
    value = jnp.zeros(references.config.nr)
    cell_variable = fvm.CellVariable(value=value, dr=references.geo.dr)
    # Overconstrain the left
    with self.assertRaises(AssertionError):
      dataclasses.replace(  # pytype: disable=wrong-arg-types  # dataclasses-replace-types
          cell_variable, left_face_constraint=1.0, right_face_constraint=2.0
      )
    # Overconstrain the right
    with self.assertRaises(AssertionError):
      dataclasses.replace(  # pytype: disable=wrong-arg-types  # dataclasses-replace-types
          cell_variable,
          right_face_constraint=3.0,
          right_face_grad_constraint=4.0,
      )

  @parameterized.parameters([
      dict(
          seed=20221114,
          references_getter=torax_refs.circular_references,
      ),
      dict(
          seed=20221114,
          references_getter=torax_refs.chease_references_Ip_from_chease,
      ),
      dict(
          seed=20221114,
          references_getter=torax_refs.chease_references_Ip_from_config,
      ),
  ])
  def test_face_grad_constraints(self, seed, references_getter):
    """Test that CellVariable.face_grad solves constrained problems."""
    references = references_getter()

    # Use ref_config to configure size, so we can also use ref_geo
    dim = references.config.nr
    value = jnp.zeros(dim)

    rng_state = jax.random.PRNGKey(seed)
    del seed  # Make sure seed isn't accidentally re-used
    x = jax.random.normal(rng_state, (dim - 2,))
    del rng_state  # Make sure rng_state isn't accidentally re-used
    # Put random values in the interior cells, make sure they are not used
    value = value.at[1:-1].set(x)
    # Make right cell different than left cell, so test catches bugs that
    # use the wrong end of the array
    value = value.at[-1].set(1)
    cell_variable = fvm.CellVariable(value=value, dr=references.geo.dr)

    # Left side, face value constraint
    left_value = dataclasses.replace(  # pytype: disable=wrong-arg-types  # dataclasses-replace-types
        cell_variable, left_face_constraint=1.0, left_face_grad_constraint=None
    )
    self.assertEqual(
        left_value.face_grad()[0], -1.0 / (0.5 * references.geo.dr)
    )

    # Left side, face grad constraint
    left_grad = dataclasses.replace(  # pytype: disable=wrong-arg-types  # dataclasses-replace-types
        cell_variable, left_face_constraint=None, left_face_grad_constraint=1.0
    )
    self.assertEqual(left_grad.face_grad()[0], 1.0)

    # Right side, face value constraint
    right_value = dataclasses.replace(  # pytype: disable=wrong-arg-types  # dataclasses-replace-types
        cell_variable,
        right_face_constraint=2.0,
        right_face_grad_constraint=None,
    )
    self.assertEqual(
        right_value.face_grad()[-1], 1.0 / (0.5 * references.geo.dr)
    )

    # Right side, face grad constraint
    right_grad = dataclasses.replace(  # pytype: disable=wrong-arg-types  # dataclasses-replace-types
        cell_variable,
        right_face_constraint=None,
        right_face_grad_constraint=1.0,
    )
    self.assertEqual(right_grad.face_grad()[-1], 1.0)

  @parameterized.parameters([
      dict(num_cells=2, theta_imp=0, time_steps=29),
      dict(num_cells=3, theta_imp=0.5, time_steps=21),
      dict(num_cells=4, theta_imp=1.0, time_steps=34),
  ])
  def test_leftward_convection(self, num_cells, theta_imp, time_steps):
    """Tests that leftward convection spreads the right boundary value."""
    num_faces = num_cells + 1
    right_boundary = jnp.array((1.0, -2.0))
    dr = jnp.array(1.0)
    x_0 = fvm.CellVariable(
        value=jnp.zeros(num_cells),
        dr=dr,
        right_face_grad_constraint=None,
        right_face_constraint=right_boundary[0],
    )
    x_1 = fvm.CellVariable(
        value=jnp.zeros(num_cells),
        dr=dr,
        right_face_grad_constraint=None,
        right_face_constraint=right_boundary[1],
    )
    x = (x_0, x_1)
    # Not deeply investigated, but dt = 1. seems unstable for explicit method.
    dt = jnp.array(1.0 - 0.5 * (theta_imp == 0))
    transient_cell_i = jnp.ones(num_cells)
    transient_cell = (transient_cell_i, transient_cell_i)
    # Use convection leftward everywhere so the right boundary condition will
    # flow across the whole mesh
    v_face_i = -jnp.ones(num_faces)
    v_face = (v_face_i, v_face_i)
    coeffs = fvm.Block1DCoeffs(
        transient_out_cell=transient_cell,
        transient_in_cell=transient_cell,
        v_face=v_face,
    )
    for _ in range(time_steps):
      x = implicit_solve_block.implicit_solve_block(
          x_old=x,
          x_new_guess=x,
          dt=dt,
          coeffs_old=coeffs,
          # Assume no time-dependent params.
          coeffs_new=coeffs,
          theta_imp=theta_imp,
      )

    np.testing.assert_allclose(x[0].value, right_boundary[0])
    np.testing.assert_allclose(x[1].value, right_boundary[1])

  @parameterized.parameters([
      dict(theta_imp=0.0),
      dict(theta_imp=0.5),
      dict(theta_imp=1.0),
  ])
  def test_implicit_source_cross(self, theta_imp):
    """Tests that implicit source cross terms act on sub-timestep scale."""

    # We model the evolution of two scalars, x and y:
    # x(0) = 0, y(0) = 0
    # dx / dt = 1, dy /dt = x.
    #
    # The analytical solution to this is
    # x(t) = t
    # y(t) = t^2 / 2.
    #
    # Now consider using a differential equation solver to step from t=0 to
    # t=delta_t.
    # An explicit solver, or an implicit solver using an explicit source term
    # to model the dependence of y on x, will have y(delta_t) = 0 because
    # x(0)=0. This approach will need a second time step before y becomes
    # nonzero.
    # An implicit solver using an implicit source term will correctly have
    # y > 0 on the first step.
    #
    # Mapping this onto the terminology of `stepper.implicit_solve_block`, we
    # use a grid with only one cell per channel, with one channel representing
    # x and the other representing y.

    # We have to use 2 cells to avoid the unsupported corner case where the
    # mesh consists of only one cell, with the same cell affected by both
    # boundary conditions.
    # For the purposes of this test, both cells model the same scalar, so
    # it's OK to look at either cell in isolation. Since there is 0 diffusion
    # and 0 convection the two cells don't interact.
    num_cells = 2

    num_faces = num_cells + 1
    dt = jnp.array(1.0)
    dx = jnp.array(1.0)
    transient_cell_i = jnp.ones(num_cells)
    transient_cell = (transient_cell_i, transient_cell_i)
    d_face_i = jnp.zeros(num_cells + 1)
    d_face = (d_face_i, d_face_i)
    v_face_i = jnp.zeros(num_faces)
    v_face = (v_face_i, v_face_i)
    source_mat_i = jnp.zeros(num_cells)
    right_boundary = jnp.array(0.0)

    kwargs = {
        'dt': dt,
        'theta_imp': theta_imp,
    }

    # Make x start to increase in channel `start` and drive an increase in the
    # other channel.
    # Exercise both directions to make sure we test both off-diagonal blocks of
    # the solver.
    for start in [0, 1]:
      # Make both x_0 and x_1 start at 0
      x_0 = fvm.CellVariable(
          value=jnp.zeros(num_cells),
          dr=dx,
          right_face_grad_constraint=None,
          right_face_constraint=right_boundary,
      )
      x_1 = fvm.CellVariable(
          value=jnp.zeros(num_cells),
          dr=dx,
          right_face_grad_constraint=None,
          right_face_constraint=right_boundary,
      )
      x = (x_0, x_1)

      # Mark the starting channel drive the destination channel
      source_mat_01 = jnp.ones(num_cells) * start
      source_mat_10 = jnp.ones(num_cells) * (1 - start)
      source_mat_cell = (
          (source_mat_i, source_mat_01),
          (source_mat_10, source_mat_i),
      )
      # Make the starting channel increase during the time step
      source_0 = jnp.ones(num_cells) * (1 - start)
      source_1 = jnp.ones(num_cells) * start
      source_cell = (source_0, source_1)
      coeffs = fvm.Block1DCoeffs(
          transient_out_cell=transient_cell,
          transient_in_cell=transient_cell,
          d_face=d_face,
          v_face=v_face,
          source_mat_cell=source_mat_cell,
          source_cell=source_cell,
      )

      x = implicit_solve_block.implicit_solve_block(
          x_old=x,
          x_new_guess=x,
          coeffs_old=coeffs,
          # Assume no time-dependent params.
          coeffs_new=coeffs,
          **kwargs,
      )

      if theta_imp == 0.0:
        # For explicit method, the source terms are applied at t=0, when
        # u[start] == 0. So they should have no effect
        np.testing.assert_allclose(x[1 - start].value, 0.0)
      else:
        # By t=1, u[start] is greater than 0, and the implicit source terms
        # should also drive u[1 - start] to be greater than 0
        self.assertGreater(x[1 - start].value.min(), 0.0)

  @parameterized.parameters([
      dict(num_cells=2, theta_imp=0, time_steps=29),
      dict(num_cells=3, theta_imp=0.5, time_steps=21),
      dict(num_cells=4, theta_imp=1.0, time_steps=34),
  ])
  def test_nonlinear_solve_block_loss_minimum(
      self, num_cells, theta_imp, time_steps
  ):
    """Tests that the linear solution for a linear problem yields zero residual and loss."""
    config = config_lib.Config(
        nr=num_cells,
        Qei_mult=0,
        Ptot=0,
        el_heat_eq=False,
        set_pedestal=False,
        solver=config_lib.SolverConfig(
            predictor_corrector=False,
            theta_imp=theta_imp,
        ),
        transport=config_lib.TransportConfig(
            transport_model='constant',
            chimin=0,
            chii_const=1,
        ),
        sources=dict(
            fusion_heat_source=source_config.SourceConfig(
                source_type=source_config.SourceType.ZERO,
            ),
            ohmic_heat_source=source_config.SourceConfig(
                source_type=source_config.SourceType.ZERO,
            ),
        ),
    )
    geo = geometry.build_circular_geometry(config)
    dynamic_config_slice = config_slice.build_dynamic_config_slice(config)
    static_config_slice = config_slice.build_static_config_slice(config)
    sources = source_profiles_lib.Sources()
    core_profiles = initial_states.initial_core_profiles(config, geo, sources)
    evolving_names = tuple(['temp_ion'])
    explicit_source_profiles = source_profiles_lib.build_source_profiles(
        sources=source_profiles_lib.Sources(),
        dynamic_config_slice=dynamic_config_slice,
        geo=geo,
        core_profiles=core_profiles,
        explicit=True,
    )
    transport_model = transport_model_factory.construct(config)
    coeffs = calc_coeffs.calc_coeffs(
        core_profiles=core_profiles,
        evolving_names=evolving_names,
        geo=geo,
        dynamic_config_slice=dynamic_config_slice,
        static_config_slice=static_config_slice,
        transport_model=transport_model,
        explicit_source_profiles=explicit_source_profiles,
        sources=sources,
        use_pereverzev=False,
    )
    # dt well under the explicit stability limit for dx=1 and chi=1
    dt = jnp.array(0.2)
    # initialize x_new for timestepping
    x_new = (core_profiles.temp_ion,)
    for _ in range(time_steps):
      x_old = copy.deepcopy(x_new)
      x_new = implicit_solve_block.implicit_solve_block(
          x_old=x_old,
          x_new_guess=x_new,
          coeffs_old=coeffs,
          # Assume no time-dependent params.
          coeffs_new=coeffs,
          dt=dt,
          theta_imp=theta_imp,
      )

      # When the coefficients are kept constant, the loss
      # should just be a quadratic bowl with the linear
      # solution as the minimum with approximately zero residual
      # core_profiles_t_plus_dt is not updated since coeffs stay constant here
      loss, _ = residual_and_loss.theta_method_block_loss(
          x_new_guess_vec=jnp.concatenate([var.value for var in x_new]),
          x_old=x_old,
          core_profiles_t_plus_dt=core_profiles,
          evolving_names=evolving_names,
          geo=geo,
          dynamic_config_slice_t_plus_dt=dynamic_config_slice,
          static_config_slice=config_slice.build_static_config_slice(config),
          dt=dt,
          coeffs_old=coeffs,
          transport_model=transport_model,
          sources=sources,
          explicit_source_profiles=explicit_source_profiles,
      )

      residual, _ = residual_and_loss.theta_method_block_residual(
          x_new_guess_vec=jnp.concatenate([var.value for var in x_new]),
          x_old=x_old,
          core_profiles_t_plus_dt=core_profiles,
          evolving_names=evolving_names,
          geo=geo,
          dynamic_config_slice_t_plus_dt=dynamic_config_slice,
          static_config_slice=config_slice.build_static_config_slice(config),
          dt=dt,
          coeffs_old=coeffs,
          transport_model=transport_model,
          sources=sources,
          explicit_source_profiles=explicit_source_profiles,
      )

      np.testing.assert_allclose(loss, 0.0, atol=1e-7)
      np.testing.assert_allclose(residual, 0.0, atol=1e-7)

  def test_implicit_solve_block_uses_updated_boundary_conditions(self):
    """Tests that updated boundary conditions affect x_new."""
    # Create a system with diffusive transport and no sources. When initialized
    # flat, x_new should remain zero unless boundary conditions change.
    num_cells = 2
    config = config_lib.Config(
        nr=num_cells,
        Qei_mult=0,
        Ptot=0,
        el_heat_eq=False,
        set_pedestal=False,
        solver=config_lib.SolverConfig(
            predictor_corrector=False,
            theta_imp=1.0,
        ),
        transport=config_lib.TransportConfig(
            transport_model='constant',
            chimin=0,
            chii_const=1,
        ),
        sources=dict(
            fusion_heat_source=source_config.SourceConfig(
                source_type=source_config.SourceType.ZERO,
            ),
            ohmic_heat_source=source_config.SourceConfig(
                source_type=source_config.SourceType.ZERO,
            ),
        ),
    )
    geo = geometry.build_circular_geometry(config)
    dynamic_config_slice = config_slice.build_dynamic_config_slice(config)
    static_config_slice = config_slice.build_static_config_slice(config)
    transport_model = transport_model_factory.construct(
        config,
    )
    sources = source_profiles_lib.Sources()
    initial_core_profiles = initial_states.initial_core_profiles(
        config, geo, sources
    )
    explicit_source_profiles = source_profiles_lib.build_source_profiles(
        sources=sources,
        dynamic_config_slice=dynamic_config_slice,
        geo=geo,
        core_profiles=initial_core_profiles,
        explicit=True,
    )

    dt = jnp.array(1.0)
    evolving_names = tuple(['temp_ion'])

    coeffs = calc_coeffs.calc_coeffs(
        core_profiles=initial_core_profiles,
        evolving_names=evolving_names,
        geo=geo,
        dynamic_config_slice=dynamic_config_slice,
        static_config_slice=static_config_slice,
        transport_model=transport_model,
        explicit_source_profiles=explicit_source_profiles,
        sources=sources,
        use_pereverzev=False,
    )
    initial_right_boundary = jnp.array(0.0)
    x_0 = fvm.CellVariable(
        value=jnp.zeros(num_cells),
        dr=jnp.array(1.0),
        right_face_grad_constraint=None,
        right_face_constraint=initial_right_boundary,
    )
    # Run with different theta_imp values.
    for theta_imp in [0.0, 0.5, 1.0]:
      x_new = implicit_solve_block.implicit_solve_block(
          x_old=(x_0,),
          x_new_guess=(x_0,),
          coeffs_old=coeffs,
          # Assume no time-dependent params.
          coeffs_new=coeffs,
          dt=dt,
          theta_imp=theta_imp,
      )
      # No matter what theta_imp is used, the x_new will be all 0s because there
      # is no source and the boundaries are set to 0.
      np.testing.assert_allclose(x_new[0].value, 0.0)

    # If we run with an updated boundary condition applied at time t=dt, then
    # we should get non-zero values from the implicit terms.
    final_right_boundary = jnp.array(1.0)
    x_1 = dataclasses.replace(x_0, right_face_constraint=final_right_boundary)
    # However, the explicit terms (when theta_imp = 0), should still be all 0.
    x_new = implicit_solve_block.implicit_solve_block(
        x_old=(x_0,),
        x_new_guess=(x_1,),
        coeffs_old=coeffs,
        # Assume no time-dependent params.
        coeffs_new=coeffs,
        dt=dt,
        theta_imp=0.0,
    )
    np.testing.assert_allclose(x_new[0].value, 0.0)
    # x_new should still have the updated boundary conditions though.
    np.testing.assert_allclose(
        x_new[0].right_face_constraint, final_right_boundary
    )
    # And when theta_imp is > 0, the values should be > 0.
    x_new = implicit_solve_block.implicit_solve_block(
        x_old=(x_0,),
        x_new_guess=(x_1,),
        coeffs_old=coeffs,
        # Assume no time-dependent params.
        coeffs_new=coeffs,
        dt=dt,
        theta_imp=0.5,
    )
    self.assertGreater(x_new[0].value.min(), 0.0)

  def test_theta_residual_uses_updated_boundary_conditions(self):
    # Create a system with diffusive transport and no sources. When initialized
    # flat, residual should remain zero unless boundary conditions change.
    num_cells = 2
    config = config_lib.Config(
        nr=num_cells,
        Qei_mult=0,
        Ptot=0,
        el_heat_eq=False,
        set_pedestal=False,
        solver=config_lib.SolverConfig(
            predictor_corrector=False,
            theta_imp=0.0,
        ),
        transport=config_lib.TransportConfig(
            transport_model='constant',
            chimin=0,
            chii_const=1,
        ),
        sources=dict(
            fusion_heat_source=source_config.SourceConfig(
                source_type=source_config.SourceType.ZERO,
            ),
            ohmic_heat_source=source_config.SourceConfig(
                source_type=source_config.SourceType.ZERO,
            ),
        ),
    )
    geo = geometry.build_circular_geometry(config)
    dynamic_config_slice = config_slice.build_dynamic_config_slice(config)
    static_config_slice_theta0 = config_slice.build_static_config_slice(config)
    static_config_slice_theta05 = dataclasses.replace(
        static_config_slice_theta0,
        solver=dataclasses.replace(
            static_config_slice_theta0.solver, theta_imp=0.5
        ),
    )

    transport_model = transport_model_factory.construct(
        config,
    )
    sources = source_profiles_lib.Sources()
    initial_core_profiles = initial_states.initial_core_profiles(
        config, geo, sources
    )
    explicit_source_profiles = source_profiles_lib.build_source_profiles(
        sources=sources,
        dynamic_config_slice=dynamic_config_slice,
        geo=geo,
        core_profiles=initial_core_profiles,
        explicit=True,
    )

    dt = jnp.array(1.0)
    evolving_names = tuple(['temp_ion'])

    coeffs_old = calc_coeffs.calc_coeffs(
        core_profiles=initial_core_profiles,
        evolving_names=evolving_names,
        geo=geo,
        dynamic_config_slice=dynamic_config_slice,
        static_config_slice=static_config_slice_theta05,
        transport_model=transport_model,
        explicit_source_profiles=explicit_source_profiles,
        sources=sources,
        use_pereverzev=False,
    )

    initial_right_boundary = jnp.array(0.0)
    x_0 = fvm.CellVariable(
        value=jnp.zeros(num_cells),
        dr=jnp.array(1.0),
        right_face_grad_constraint=None,
        right_face_constraint=initial_right_boundary,
    )
    core_profiles_t_plus_dt = initial_states.initial_core_profiles(config, geo)
    core_profiles_t_plus_dt = dataclasses.replace(
        core_profiles_t_plus_dt,
        temp_ion=x_0,
    )

    with self.subTest('static_boundary_conditions'):
      # When the boundary conditions are not time-dependent and stay at 0,
      # with diffusive transport and zero transport, then the state will stay
      # at all 0, and the residual should be 0.
      residual, _ = residual_and_loss.theta_method_block_residual(
          x_new_guess_vec=x_0.value,
          x_old=(x_0,),
          core_profiles_t_plus_dt=core_profiles_t_plus_dt,
          evolving_names=evolving_names,
          geo=geo,
          dynamic_config_slice_t_plus_dt=dynamic_config_slice,
          static_config_slice=static_config_slice_theta05,
          dt=dt,
          coeffs_old=coeffs_old,
          transport_model=transport_model,
          sources=sources,
          explicit_source_profiles=explicit_source_profiles,
      )
      np.testing.assert_allclose(residual, 0.0)
    with self.subTest('updated_boundary_conditions'):
      # When the boundary condition updates at time t+dt, then the implicit part
      # of the update would generate a residual. When theta_imp is 0, the
      # residual would still be 0.
      final_right_boundary = jnp.array(1.0)
      residual, _ = residual_and_loss.theta_method_block_residual(
          x_new_guess_vec=x_0.value,
          x_old=(x_0,),
          core_profiles_t_plus_dt=dataclasses.replace(
              core_profiles_t_plus_dt,
              temp_ion=dataclasses.replace(
                  x_0, right_face_constraint=final_right_boundary
              ),
          ),
          evolving_names=evolving_names,
          geo=geo,
          dynamic_config_slice_t_plus_dt=dynamic_config_slice,
          static_config_slice=static_config_slice_theta0,
          dt=dt,
          coeffs_old=coeffs_old,
          transport_model=transport_model,
          sources=sources,
          explicit_source_profiles=explicit_source_profiles,
      )
      np.testing.assert_allclose(residual, 0.0)
      # But when theta_imp > 0, the residual should be non-zero.
      residual, _ = residual_and_loss.theta_method_block_residual(
          x_new_guess_vec=x_0.value,
          x_old=(x_0,),
          core_profiles_t_plus_dt=dataclasses.replace(
              core_profiles_t_plus_dt,
              temp_ion=dataclasses.replace(
                  x_0, right_face_constraint=final_right_boundary
              ),
          ),
          evolving_names=evolving_names,
          dt=dt,
          geo=geo,
          dynamic_config_slice_t_plus_dt=dynamic_config_slice,
          static_config_slice=static_config_slice_theta05,
          coeffs_old=coeffs_old,
          transport_model=transport_model,
          sources=sources,
          explicit_source_profiles=explicit_source_profiles,
      )
      self.assertGreater(jnp.abs(jnp.sum(residual)), 0.0)


if __name__ == '__main__':
  absltest.main()
