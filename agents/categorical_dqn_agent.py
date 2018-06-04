#
# Copyright (c) 2017 Intel Corporation 
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
from typing import Union

from agents.value_optimization_agent import ValueOptimizationAgent
import numpy as np
from agents.dqn_agent import DQNAgentParameters, DQNNetworkParameters, DQNAlgorithmParameters
from configurations import OutputTypes, AgentParameters
from core_types import StateType
from exploration_policies.e_greedy import EGreedyParameters
from memories.experience_replay import ExperienceReplayParameters
from schedules import LinearSchedule


class CategoricalDQNNetworkParameters(DQNNetworkParameters):
    def __init__(self):
        super().__init__()
        self.output_types = [OutputTypes.CategoricalQ]
        self.neon_support = False


class CategoricalDQNAlgorithmParameters(DQNAlgorithmParameters):
    def __init__(self):
        super().__init__()
        self.v_min = -10.0
        self.v_max = 10.0
        self.atoms = 51


class CategoricalDQNExplorationParameters(EGreedyParameters):
    def __init__(self):
        super().__init__()
        self.epsilon_schedule = LinearSchedule(1, 0.01, 1000000)
        self.evaluation_epsilon = 0.001


class CategoricalDQNAgentParameters(AgentParameters):
    def __init__(self):
        super().__init__(algorithm=CategoricalDQNAlgorithmParameters(),
                         exploration=CategoricalDQNExplorationParameters(),
                         memory=ExperienceReplayParameters(),
                         networks={"main": CategoricalDQNNetworkParameters()})

    @property
    def path(self):
        return 'agents.categorical_dqn_agent:CategoricalDQNAgent'


# Categorical Deep Q Network - https://arxiv.org/pdf/1707.06887.pdf
class CategoricalDQNAgent(ValueOptimizationAgent):
    def __init__(self, agent_parameters, parent: Union['LevelManager', 'CompositeAgent']=None):
        super().__init__(agent_parameters, parent)
        self.z_values = np.linspace(self.ap.algorithm.v_min, self.ap.algorithm.v_max, self.ap.algorithm.atoms)

    def distribution_prediction_to_q_values(self, prediction):
        return np.dot(prediction, self.z_values)

    # prediction's format is (batch,actions,atoms)
    def get_all_q_values_for_states(self, states: StateType):
        prediction = self.get_prediction(states)
        return self.distribution_prediction_to_q_values(prediction)

    def learn_from_batch(self, batch):
        current_states, next_states, actions, rewards, game_overs, _ = self.extract_batch(batch, 'main')

        # for the action we actually took, the error is calculated by the atoms distribution
        # for all other actions, the error is 0
        distributed_q_st_plus_1 = self.networks['main'].target_network.predict(next_states)
        # initialize with the current prediction so that we will
        TD_targets = self.networks['main'].online_network.predict(current_states)

        # only update the action that we have actually done in this transition
        target_actions = np.argmax(self.distribution_prediction_to_q_values(distributed_q_st_plus_1), axis=1)
        m = np.zeros((self.ap.network_wrappers['main'].batch_size, self.z_values.size))

        batches = np.arange(self.ap.network_wrappers['main'].batch_size)
        for j in range(self.z_values.size):
            tzj = np.fmax(np.fmin(rewards + (1.0 - game_overs) * self.ap.algorithm.discount * self.z_values[j],
                                  self.z_values[self.z_values.size - 1]),
                          self.z_values[0])
            bj = (tzj - self.z_values[0])/(self.z_values[1] - self.z_values[0])
            u = (np.ceil(bj)).astype(int)
            l = (np.floor(bj)).astype(int)
            m[batches, l] = m[batches, l] + (distributed_q_st_plus_1[batches, target_actions, j] * (u - bj))
            m[batches, u] = m[batches, u] + (distributed_q_st_plus_1[batches, target_actions, j] * (bj - l))
        # total_loss = cross entropy between actual result above and predicted result for the given action
        TD_targets[batches, actions] = m

        result = self.networks['main'].train_and_sync_networks(current_states, TD_targets)
        total_loss, losses, unclipped_grads = result[:3]

        return total_loss, losses, unclipped_grads

