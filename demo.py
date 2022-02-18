import argparse

import gym

# Arguments
parser = argparse.ArgumentParser(description='Demo Go Environment')
parser.add_argument('--boardsize', type=int, default=3)
parser.add_argument('--komi', type=float, default=0)
args = parser.parse_args()

# Initialize environment
go_env = gym.make('gym_go:go-v1', size=args.boardsize, komi=args.komi)
go_env.reset()

print(go_env.observation_space)
print(go_env.action_space)

# Game loop
done = False
while not done:
    action = (0,2)
    state, reward, done, info = go_env.step(action)
    print(state[:-9].shape)
    print(state[-9:].shape)
    state = go_env.reset()
    print(state[:-9].shape)
    print(state[-9:].shape)
    go_env.render(mode="human")

    if go_env.game_ended():
        break
    action = go_env.uniform_random_action()
    state, reward, done, info = go_env.step(action)
go_env.render(mode="human")
