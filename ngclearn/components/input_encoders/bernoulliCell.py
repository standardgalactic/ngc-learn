from ngcsimlib.component import Component
from jax import numpy as jnp, random, jit
from functools import partial
import time

@jit
def update_times(t, s, tols):
    """
    Updates time-of-last-spike (tols) variable.

    Args:
        t: current time (a scalar/int value)

        s: binary spike vector

        tols: current time-of-last-spike variable

    Returns:
        updated tols variable
    """
    _tols = (1. - s) * tols + (s * t)
    return _tols

@jit
def sample_bernoulli(dkey, data):
    """
    Samples a Bernoulli spike train on-the-fly

    Args:
        dkey: JAX key to drive stochasticity/noise
        
        data: sensory data (vector/matrix)

    Returns:
        binary spikes
    """
    s_t = random.bernoulli(dkey, p=data).astype(jnp.float32)
    return s_t

class BernoulliCell(Component):
    """
    A Bernoulli cell that produces Bernoulli-distributed spikes on-the-fly.

    Args:
        name: the string name of this cell

        n_units: number of cellular entities (neural population size)

        key: PRNG key to control determinism of any underlying synapses
            associated with this cell

        useVerboseDict: triggers slower, verbose dictionary mode (Default: False)
    """

    ## Class Methods for Compartment Names
    @classmethod
    def inputCompartmentName(cls):
        return 'in'

    @classmethod
    def outputCompartmentName(cls):
        return 'out'

    @classmethod
    def timeOfLastSpikeCompartmentName(cls):
        return 'tols'

    ## Bind Properties to Compartments for ease of use
    @property
    def inputCompartment(self):
        return self.compartments.get(self.inputCompartmentName(), None)

    @inputCompartment.setter
    def inputCompartment(self, inp):
        self.compartments[self.inputCompartmentName()] = inp

    @property
    def outputCompartment(self):
        return self.compartments.get(self.outputCompartmentName(), None)

    @outputCompartment.setter
    def outputCompartment(self, out):
        self.compartments[self.outputCompartmentName()] = out

    @property
    def timeOfLastSpike(self):
        return self.compartments.get(self.timeOfLastSpikeCompartmentName(), None)

    @timeOfLastSpike.setter
    def timeOfLastSpike(self, t):
        self.compartments[self.timeOfLastSpikeCompartmentName()] = t

    # Define Functions
    def __init__(self, name, n_units, key=None, useVerboseDict=False, **kwargs):
        super().__init__(name, useVerboseDict, **kwargs)

        ##Random Number Set up
        self.key = key
        if self.key is None:
            self.key = random.PRNGKey(time.time_ns())

        ##Layer Size Setup
        self.batch_size = 1
        self.n_units = n_units
        self.reset()

    def verify_connections(self):
        pass

    def advance_state(self, t, dt, **kwargs):
        self.key, *subkeys = random.split(self.key, 2)

        self.outputCompartment = sample_bernoulli(subkeys[0], data=self.inputCompartment)
        #self.timeOfLastSpike = (1 - self.outputCompartment) * self.timeOfLastSpike + (self.outputCompartment * t)
        self.timeOfLastSpike = update_times(t, self.outputCompartment, self.timeOfLastSpike)

    def reset(self, **kwargs):
        self.inputCompartment = None
        self.outputCompartment = jnp.zeros((self.batch_size, self.n_units)) #None
        self.timeOfLastSpike = jnp.zeros((self.batch_size, self.n_units))

    def save(self, **kwargs):
        pass
