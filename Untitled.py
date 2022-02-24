#!/usr/bin/env python
# coding: utf-8

# In[1]:


import os
from collections import OrderedDict, deque, namedtuple
from typing import List, Tuple

import gym
import numpy as np
import torch
from pytorch_lightning import LightningModule, Trainer
from pytorch_lightning.utilities import DistributedType
from torch import Tensor, nn
from torch.optim import Adam, Optimizer
from torch.utils.data import DataLoader
from torch.utils.data.dataset import IterableDataset
from pytorch_lightning.loggers import TensorBoardLogger
import csv

PATH_DATASETS = os.environ.get("PATH_DATASETS", ".")
AVAIL_GPUS = min(1, torch.cuda.device_count())
print(AVAIL_GPUS)

# In[2]:




class DQN(nn.Module):
    """Simple MLP network."""

    def __init__(self, obs_size: int, n_actions: int, hidden_size: int = 50):
        """
        Args:
            obs_size: observation/state size of the environment
            n_actions: number of discrete actions available in the environment
            hidden_size: size of hidden layers
        """
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(obs_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, n_actions),
            nn.Softmax()
        )

    def forward(self, x):
        return self.net(x.float())


# In[3]:


# Named tuple for storing experience steps gathered in training
Experience = namedtuple(
    "Experience",
    field_names=["state", "action", "reward", "done", "new_state"],
)

class ReplayBuffer:
    """Replay Buffer for storing past experiences allowing the agent to learn from them.

    Args:
        capacity: size of the buffer
    """

    def __init__(self, capacity: int) -> None:
        self.buffer = deque(maxlen=capacity)

    def __len__(self) -> None:
        return len(self.buffer)

    def append(self, experience: Experience) -> None:
        """Add experience to the buffer.

        Args:
            experience: tuple (state, action, reward, done, new_state)
        """
        self.buffer.append(experience)

    def sample(self, batch_size: int) -> Tuple:
        print("Batch Size : ", batch_size)
        indices = np.random.choice(len(self.buffer), batch_size, replace=False)
        states, actions, rewards, dones, next_states = zip(*(self.buffer[idx] for idx in indices))

        return (
            np.array(states),
            np.array(actions),
            np.array(rewards, dtype=np.float32),
            np.array(dones, dtype=np.bool),
            np.array(next_states),
        )


# In[4]:


class RLDataset(IterableDataset):
    """Iterable Dataset containing the ExperienceBuffer which will be updated with new experiences during training.

    Args:
        buffer: replay buffer
        sample_size: number of experiences to sample at a time
    """

    def __init__(self, buffer: ReplayBuffer, sample_size: int = 200) -> None:
        self.buffer = buffer
        self.sample_size = sample_size

    def __iter__(self) -> Tuple:
        if self.buffer.__len__() > self.sample_size:
            states, actions, rewards, dones, new_states = self.buffer.sample(self.sample_size)
        else:
            states, actions, rewards, dones, new_states = self.buffer.sample(self.buffer.__len__() )
        for i in range(len(dones)):
            yield states[i], actions[i], rewards[i], dones[i], new_states[i]


# In[5]:

def pickFileName():
    
    files = os.listdir('/log/trainingvals/')
    
    return '{}.csv'.format(len(files)+1)


import random
class Agent:
    """Base Agent class handeling the interaction with the environment."""

    def __init__(self, env: gym.Env, replay_buffer: ReplayBuffer) -> None:
        """
        Args:
            env: training environment
            replay_buffer: replay buffer storing experiences
        """
        self.env = env
        self.replay_buffer = replay_buffer
        self.reset()
        self.state = self.env.reset()

    def reset(self) -> None:
        """Resents the environment and updates the state."""
        self.state = self.env.reset()

    def get_action(self, net: nn.Module, epsilon: float, device: str) -> int:
        """Using the given network, decide what action to carry out using an epsilon-greedy policy.

        Args:
            net: DQN network
            epsilon: value to determine likelihood of taking a random action
            device: current device

        Returns:
            action
        """
        if np.random.random() < epsilon:
            action = random.choice([i for i, e in enumerate(self.env.valid_moves()) if e == 1])
        else:
            state = torch.tensor([self.state])

            if device not in ["cpu"]:
                state = state.cuda(device)

            q_values = net(state)
            print("q_values : ",q_values)
            #masking invalid actions
            mask = torch.tensor(self.env.valid_moves(),dtype=torch.float)
            print("mask : ",mask)
            masked = q_values * mask
            print("masked : ",masked)
            if(torch.count_nonzero(masked).item() == 0):
                print("ALL ZEROS! Picking at random")
                _, action = torch.max(mask, dim=0)
            else:
                _, action = torch.max(masked, dim=1)
            print("picked : ",action)
            action = int(action.item())
            print("action : ",action)
        return action

    @torch.no_grad()
    def play_step(
        self,
        net: nn.Module,
        epsilon: float = 0.0,
        device: str = "cpu",
    ) -> Tuple[float, bool]:
        """Carries out a single interaction step between the agent and the environment.

        Args:
            net: DQN network
            epsilon: value to determine likelihood of taking a random action
            device: current device

        Returns:
            reward, done
        """

        action = self.get_action(net, epsilon, device)

        # do step in the environment
        new_state, reward, done, _ = self.env.step(action)
        print("done , ",done)

        exp = Experience(self.state, action, reward, done, new_state)

        self.replay_buffer.append(exp)

        self.state = new_state
        if done:
            print("resetting")
            self.reset()
        return reward, done


# In[6]:


class DQNLightning(LightningModule):
    """Basic DQN Model."""

    def __init__(
        self, 
        batch_size: int = 16,
        lr: float = 1e-3,
        env: str = "gym_go:go-v1",
        gamma: float = 0.99,
        sync_rate: int = 100,
        replay_size: int = 1000,
        warm_start_size: int = 1000,
        eps_last_frame: int = 1000,
        eps_start: float = 1.0,
        eps_end: float = 0.01,
        episode_length: int = 200,
        warm_start_steps: int = 2000,
    ) -> None:
        """
        Args:
            batch_size: size of the batches")
            lr: learning rate
            env: gym environment tag
            gamma: discount factor
            sync_rate: how many frames do we update the target network
            replay_size: capacity of the replay buffer
            warm_start_size: how many samples do we use to fill our buffer at the start of training
            eps_last_frame: what frame should epsilon stop decaying
            eps_start: starting value of epsilon
            eps_end: final value of epsilon
            episode_length: max length of an episode
            warm_start_steps: max episode reward in the environment
        """
        super().__init__()
        self.save_hyperparameters()

        self.env = gym.make('gym_go:go-v1', size=5, komi=6.5)
        obs_size = self.env.observation_space.shape[0]
        n_actions = self.env.action_space.n

        self.net = DQN(obs_size, n_actions)
        self.target_net = DQN(obs_size, n_actions)

        self.buffer = ReplayBuffer(self.hparams.replay_size)
        self.agent = Agent(self.env, self.buffer)
        self.total_reward = 0
        self.episode_reward = 0
        self.populate(self.hparams.warm_start_steps)

    def populate(self, steps: int = 2000) -> None:
        """Carries out several random steps through the environment to initially fill up the replay buffer with
        experiences.

        Args:
            steps: number of random steps to populate the buffer with
        """
        print("populating...")
        for i in range(steps):
            _, done = self.agent.play_step(self.net, epsilon=1.0)
            #opponent plays random
            if not done:
                _,_,done,_ = self.env.step(self.env.uniform_random_action())
                if done: 
                    self.env.reset()
        print("Finished populating")
        self.env.reset()

    def forward(self, x: Tensor) -> Tensor:
        """Passes in a state x through the network and gets the q_values of each action as an output.

        Args:
            x: environment state

        Returns:
            q values
        """
        output = self.net(x)
        return output

    def dqn_mse_loss(self, batch: Tuple[Tensor, Tensor]) -> Tensor:
        """Calculates the mse loss using a mini batch from the replay buffer.

        Args:
            batch: current mini batch of replay data

        Returns:
            loss
        """
        states, actions, rewards, dones, next_states = batch

        state_action_values = self.net(states).gather(1, actions.unsqueeze(-1)).squeeze(-1)

        with torch.no_grad():
            next_state_values = self.target_net(next_states).max(1)[0]
            next_state_values[dones] = 0.0
            next_state_values = next_state_values.detach()

        expected_state_action_values = next_state_values * self.hparams.gamma + rewards

        return nn.MSELoss()(state_action_values, expected_state_action_values)

    def training_step(self, batch: Tuple[Tensor, Tensor], nb_batch) -> OrderedDict:
        """Carries out a single step through the environment to update the replay buffer. Then calculates loss
        based on the minibatch recieved.

        Args:
            batch: current mini batch of replay data
            nb_batch: batch number

        Returns:
            Training loss and log metrics
        """

        global writer

        device = self.get_device(batch)
        epsilon = max(
            self.hparams.eps_end,
            self.hparams.eps_start - self.global_step + 1 / self.hparams.eps_last_frame,
        )

        # step through environment with agent
        print("Agent playing")
        reward, done = self.agent.play_step(self.net, epsilon, device)
        
        if not done: 
            #opponent plays random
            print("Random move")
            _,_,done2,_ = self.env.step(self.env.uniform_random_action())

            if done2:
                print("resetting")
                self.env.reset()
        else: 
            print("Agent reset, not playing random")
        
        self.episode_reward += reward

        # calculates training loss
        loss = self.dqn_mse_loss(batch)

        if self.trainer._distrib_type in {DistributedType.DP, DistributedType.DDP2}:
            loss = loss.unsqueeze(0)

        if done:
            self.total_reward = self.episode_reward
            self.episode_reward = 0

        # Soft update of target network
        if self.global_step % self.hparams.sync_rate == 0:
            self.target_net.load_state_dict(self.net.state_dict())

        log = {
            "total_reward": torch.tensor(self.total_reward).to(device),
            "reward": torch.tensor(reward).to(device),
            "train_loss": loss,
        }

        print("LOGGING!")
        self.log("total_reward", torch.tensor(self.total_reward).to(device), on_step=True, on_epoch=True, prog_bar=True, logger=True)
        self.log("reward", torch.tensor(reward).to(device), on_step=True, on_epoch=True, prog_bar=True, logger=True)
        self.log("train_loss", loss, on_step=True, on_epoch=True, prog_bar=True, logger=True)

        status = {
            "steps": torch.tensor(self.global_step).to(device),
            "total_reward": torch.tensor(self.total_reward).to(device),
        }

        writer.writerow([self.global_step, self.total_reward, loss.unsqueeze(0).item()])

        return OrderedDict({"loss": loss, "log": log, "progress_bar": status})

    def configure_optimizers(self) -> List[Optimizer]:
        """Initialize Adam optimizer."""
        optimizer = Adam(self.net.parameters(), lr=self.hparams.lr)
        return [optimizer]

    def __dataloader(self) -> DataLoader:
        """Initialize the Replay Buffer dataset used for retrieving experiences."""
        dataset = RLDataset(self.buffer, self.hparams.episode_length)
        dataloader = DataLoader(
            dataset=dataset,
            batch_size=self.hparams.batch_size,
        )
        return dataloader

    def train_dataloader(self) -> DataLoader:
        """Get train loader."""
        return self.__dataloader()

    def get_device(self, batch) -> str:
        """Retrieve device currently being used by minibatch."""
        return batch[0].device.index if self.on_gpu else "cpu"


# In[ ]:


f = open('/log/trainingvals/{}'.format(pickFileName()), 'w+')
writer = csv.writer(f)

model = DQNLightning()


tb_logger = TensorBoardLogger("/log/") 
trainer = Trainer(
    gpus=[0],
    max_epochs=50000,
    val_check_interval=100,
    logger=tb_logger,
)

trainer.fit(model)
print("F:",f)
f.close()
