from gym.envs.registration import register

register(
    id='go-v0',
    entry_point='gym_go.envs:GoEnv',
)

register(
    id='go-v1',
    entry_point='gym_go.envs:GoEnv',
    max_episode_steps=100,
)
register(
    id='go-extrahard-v0',
    entry_point='gym_go.envs:GoExtraHardEnv',
)
