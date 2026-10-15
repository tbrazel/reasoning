# Swarm experiment contract v1alpha1

This contract describes reproducible dynamical experiments over finite graphs
and simplicial complexes. It separates the mathematical substrate from agent
state, update dynamics, scheduling, interventions, observations, replication,
and execution. Belief collapse is one possible experiment family rather than a
schema assumption.

Reasoning owns experiment and run records. Noether `v0.3.1` owns exact
mathematical objects and verification used by the topology provider. A run
records the resolved Noether source commit, installation and wheel digests,
canonical substrate digest, all random seeds, exact model policy, budgets,
events, measurements, and a content replay digest. Simulated revisions replay
deterministically; model-backed digests expose any change in returned state or
provider provenance.

The runtime maps agents to vertices by default, pairwise interactions to edges,
and higher-order interactions to simplices. State may instead live on edges for
gauge-like experiments. Static and intervention-driven substrates use the same
contract; every changed substrate must be reverified before it is used.
