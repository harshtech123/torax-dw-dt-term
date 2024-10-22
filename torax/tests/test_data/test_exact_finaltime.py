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

"""test_exact_t_final: tests deterministic t_final with exact_t_final = True."""


CONFIG = {
    'runtime_params': {
        'profile_conditions': {
            # initial condition ion temperature for r=0 and r=Rmin
            'Ti': {0.0: {0.0: 8.0, 1.0: 1.0}},
            'Te': {0.0: {0.0: 8.0, 1.0: 1.0}},
            'ne_bound_right': 0.5,
            # set flat Ohmic current to provide larger range of current
            # evolution for test.
            'nu': 0,
        },
        'numerics': {
            'current_eq': True,
            'resistivity_mult': 100,  # to shorten current diffusion time
            't_final': 2,
            'exact_t_final': True,
        },
    },
    'geometry': {
        'geometry_type': 'circular',
    },
    'sources': {
        # Current sources (for psi equation)
        'jext': {},
        # Electron density sources/sink (for the ne equation).
        'generic_particle_source': {},
        'gas_puff_source': {},
        'pellet_source': {},
        # Ion and electron heat sources (for the temp-ion and temp-el eqs).
        'generic_ion_el_heat_source': {},
        'qei_source': {},
    },
    'transport': {
        'transport_model': 'qlknn',
    },
    'stepper': {
        'stepper_type': 'linear',
        'predictor_corrector': False,
        'use_pereverzev': True,
    },
    'time_step_calculator': {
        'calculator_type': 'chi',
    },
}
