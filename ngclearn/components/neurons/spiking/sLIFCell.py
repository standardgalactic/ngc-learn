from ngcsimlib.component import Component
from jax import numpy as jnp, random, jit
from functools import partial
import time, sys

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
def apply_surrogate_dfx(j, c1=0.82, c2=0.08):
    """
    Applies a surrogate (derivative) function -- often used to approximate the
    derivative through an action potential/spike's output value.

    Args:
        j: electrical current value

        c1: control coefficient 1 (unnamed factor from paper - scales current
            input; Default: 0.82 as in source paper)

        c2: control coefficient 2 (unnamed factor from paper - scales, multiplicatively
            with c1, the output the derivative surrogate; Default: 0.08 as in
            source paper)

    Returns:
        surrogate output values (same shape as j)
    """
    # sech(x) = 1/cosh(x), cosh(x) = (e^x + e^(-x))/2
    # c1 c2 sech^2(c2 j) for j > 0 and 0 for j <= 0
    mask = (j > 0.).astype(jnp.float32)
    dj = j * c2
    cosh_j = (jnp.exp(dj) + jnp.exp(-dj))/2.
    sech_j = 1./cosh_j #(cosh_x + 1e-6)
    dv_dj = sech_j * (c1 * c2) # ~deriv w.r.t. j
    return dv_dj * mask ## 0 if j < 0, otherwise, use dv/dj for j >= 0

@partial(jit, static_argnums=[3,4])
def modify_current(j, spikes, inh_weights, R_m, inh_R):
    """
    A simple function that modifies electrical current j via application of a
    scalar membrane resistance value and an approximate form of lateral inhibition.
    Note that if no inhibitory resistance is set (i.e., inh_R = 0), then no
    lateral inhibition is applied. Functionally, this routine carries out the
    following piecewise equation:

    | j * R_m - [Wi * s(t-dt)] * inh_R, if inh_R > 0
    | j * R_m, otherwise

    Args:
        j: electrical current value

        spikes: previous binary spike vector (for t-dt)

        inh_weights: lateral recurrent inhibitory synapses (typically should be
            chosen to be a scaled hollow matrix)

        R_m: membrane resistance (to multiply/scale j by)

        inh_R: inhibitory resistance to scale lateral inhibitory current by; if
            inh_R = 0, NO lateral inhibitory pressure will be applied

    Returns:
        modified electrical current value
    """
    _j = j * R_m
    if inh_R > 0.:
        _j = _j - (jnp.matmul(spikes, inh_weights) * inh_R)
    return _j

@partial(jit, static_argnums=[6,7,8,9,10,11])
def run_cell(dt, j, v, v_thr, tau_m, rfr, refract_T=1., thrGain=0.002,
             thrLeak=0.0005, rho_b = 0., sticky_spikes=False, v_min=None):
    """
    Runs leaky integrator neuronal dynamics

    Args:
        dt: integration time constant (milliseconds, or ms)

        j: electrical current value

        v: membrane potential (voltage) value (at t)

        v_thr: voltage threshold value (at t)

        tau_m: cell membrane time constant

        rfr: refractory variable vector (one per neuronal cell)

        refract_T: (relative) refractory time period (in ms; Default
            value is 1 ms)

        thrGain: the amount of threshold incremented per time step (if spike present)

        thrLeak: the amount of threshold value leaked per time step

        rho_b: sparsity factor; if > 0, will force adaptive threshold to operate
            with sparsity across a layer enforced

        sticky_spikes: if True, then spikes are pinned at value of action potential
            (i.e., 1) for as long as the relative refractory occurs (this recovers
            the source paper's core spiking process)

    Returns:
        voltage(t+dt), spikes, threshold(t+dt), updated refactory variables
    """
    mask = (rfr >= refract_T).astype(jnp.float32) # get refractory mask
    new_voltage = (v + (-v + j) * (dt / tau_m)) * mask
    if v_min is not None:
        new_voltage = jnp.maximum(v_min, new_voltage)
    #new_voltage = v + (-v) * (dt / tau_m) + j * mask
    spikes = jnp.where(new_voltage > v_thr, 1, 0)
    new_voltage = (1. - spikes) * new_voltage ## hyper-polarize cells
    ## update thresholds if applicable
    if rho_b > 0.: ## run sparsity-enforcement threshold
        dthr = jnp.sum(spikes, axis=1, keepdims=True) - 1.0
        new_thr = jnp.maximum(v_thr + dthr * rho_b, 0.025)
    else: ## run simple adaptive threshold
        thr_gain = spikes * thrGain
        thr_leak = (v_thr * thrLeak)
        new_thr = v_thr + thr_gain - thr_leak
    ## update refractory variables
    _rfr = (rfr + dt) * (1. - spikes)
    if sticky_spikes == True: ## pin refractory spikes if configured
        spikes = spikes * mask + (1. - mask)
    return new_voltage, spikes, new_thr, _rfr

class SLIFCell(Component): ## leaky integrate-and-fire cell
    """
    A spiking cell based on a simplified leaky integrate-and-fire (sLIF) model.
    This neuronal cell notably contains functionality required by the computational
    model employed by (Samadi et al., 2017, i.e., a surrogate derivative function
    and "sticky spikes") as well as the additional incorporation of an adaptive
    threshold (per unit) scheme.

    | Reference:
    | Samadi, Arash, Timothy P. Lillicrap, and Douglas B. Tweed. "Deep learning with
    | dynamic spiking neurons and fixed feedback weights." Neural computation 29.3
    | (2017): 578-602.

    Args:
        name: the string name of this cell

        n_units: number of cellular entities (neural population size)

        tau_m: membrane time constant

        R_m: membrane resistance value

        thr: base value for adaptive thresholds (initial condition for
            per-cell thresholds) that govern short-term plasticity

        inhibit_R: lateral modulation factor (DEFAULT: 6.); if >0, this will trigger
            a heuristic form of lateral inhibition via an internally integrated
            hollow matrix multiplication

        thr_persist: are adaptive thresholds persistent? (Default: False)

            :Note: depending on the value of this boolean variable:
                True = adaptive thresholds are NEVER reset upon call to reset
                False = adaptive thresholds are reset to "thr" upon call to reset

        thrGain: how much adaptive thresholds increment by

        thrLeak: how much adaptive thresholds are decremented/decayed by

        refract_T: relative refractory period time (ms; Default: 1 ms)

        rho_b: threshold sparsity factor (Default: 0)

        sticky_spikes: if True, spike variables will be pinned to action potential
            value (i.e, 1) throughout duration of the refractory period; this recovers
            a key setting used by Samadi et al., 2017

        thr_jitter: scale of uniform jitter to add to initialization of thresholds

        key: PRNG key to control determinism of any underlying random values
            associated with this cell

        useVerboseDict: triggers slower, verbose dictionary mode (Default: False)

        directory: string indicating directory on disk to save sLIF parameter
            values to (i.e., initial threshold values and any persistent adaptive
            threshold values)
    """

    ## Class Methods for Compartment Names
    @classmethod
    def inputCompartmentName(cls):
        return 'j' ## electrical current

    @classmethod
    def outputCompartmentName(cls):
        return 's' ## spike/action potential

    @classmethod
    def timeOfLastSpikeCompartmentName(cls):
        return 'tols' ## time-of-last-spike (record vector)

    @classmethod
    def voltageCompartmentName(cls):
        return 'v' ## membrane potential/voltage

    @classmethod
    def thresholdCompartmentName(cls):
        return 'thr' ## action potential threshold

    @classmethod
    def refractCompartmentName(cls):
        return 'rfr' ## refractory variable(s)

    @classmethod
    def surrogateCompartmentName(cls):
        return 'surrogate'

    ## Bind Properties to Compartments for ease of use
    @property
    def current(self):
        return self.compartments.get(self.inputCompartmentName(), None)

    @current.setter
    def current(self, inp):
        self.compartments[self.inputCompartmentName()] = inp

    @property
    def surrogate(self):
        return self.compartments.get(self.surrogateCompartmentName(), None)

    @surrogate.setter
    def surrogate(self, inp):
        self.compartments[self.surrogateCompartmentName()] = inp

    @property
    def spikes(self):
        return self.compartments.get(self.outputCompartmentName(), None)

    @spikes.setter
    def spikes(self, out):
        self.compartments[self.outputCompartmentName()] = out

    @property
    def timeOfLastSpike(self):
        return self.compartments.get(self.timeOfLastSpikeCompartmentName(), None)

    @timeOfLastSpike.setter
    def timeOfLastSpike(self, t):
        self.compartments[self.timeOfLastSpikeCompartmentName()] = t

    @property
    def voltage(self):
        return self.compartments.get(self.voltageCompartmentName(), None)

    @voltage.setter
    def voltage(self, v):
        self.compartments[self.voltageCompartmentName()] = v

    @property
    def refract(self):
        return self.compartments.get(self.refractCompartmentName(), None)

    @refract.setter
    def refract(self, rfr):
        self.compartments[self.refractCompartmentName()] = rfr

    @property
    def threshold(self):
        return self.compartments.get(self.thresholdCompartmentName(), None)

    @threshold.setter
    def threshold(self, thr):
        self.compartments[self.thresholdCompartmentName()] = thr

    # Define Functions
    def __init__(self, name, n_units, tau_m, R_m, thr, inhibit_R=0., thr_persist=False,
                 thrGain=0.0, thrLeak=0.0, rho_b=0., refract_T=0., sticky_spikes=False,
                 thr_jitter=0.05, key=None, useVerboseDict=False, directory=None, **kwargs):
        super().__init__(name, useVerboseDict, **kwargs)

        ##Random Number Set up
        self.key = key
        if self.key is None:
            self.key = random.PRNGKey(time.time_ns())

        ## membrane parameter setup (affects ODE integration)
        self.tau_m = tau_m ## membrane time constant
        self.R_m = R_m ## resistance value
        self.refract_T = refract_T #5. # 2. ## refractory period  # ms
        self.v_min = -3.
        ## variable below determines if spikes pinned at 1 during refractory period?
        self.sticky_spikes = sticky_spikes

        ## create simple recurrent inhibitory pressure
        self.inh_R = inhibit_R ## lateral inhibitory magnitude
        self.key, subkey = random.split(self.key)
        self.inh_weights = random.uniform(subkey, (n_units, n_units), minval=0.025, maxval=1.)
        MV = 1. - jnp.eye(n_units)
        self.inh_weights = self.inh_weights * MV

        ##Layer Size Setup
        self.n_units = n_units
        self.batch_size = 1

        ## adaptive threshold setup
        self.rho_b = rho_b
        self.thr_persist = thr_persist ## are adapted thresholds persistent? True (persistent)
        self.thrGain = thrGain #0.0005
        self.thrLeak = thrLeak #0.00005
        if directory is None:
            self.thr_jitter =  thr_jitter ## some random jitter to ensure thresholds start off different
            self.key, subkey = random.split(self.key)
            #self.threshold = random.uniform(subkey, (1, n_units), minval=thr, maxval=1.25 * thr)
            self.threshold0 = thr + random.uniform(subkey, (1, n_units),
                                                  minval=-self.thr_jitter, maxval=self.thr_jitter,
                                                  dtype=jnp.float32)
            self.threshold = self.threshold0 + 0 ## save initial threshold
        else:
            self.load(directory)

        self.reset()

    def verify_connections(self):
        self.metadata.check_incoming_connections(self.inputCompartmentName(), min_connections=1)

    def advance_state(self, t, dt, **kwargs):
        if self.spikes is None:
            self.spikes = jnp.zeros((self.batch_size, self.n_units))
        if self.refract is None:
            self.refract = jnp.zeros((self.batch_size, self.n_units)) + self.refract_T
        ## run one step of Euler integration over neuronal dynamics
        j_curr = self.current
        ## apply simplified inhibitory pressure
        j_curr = modify_current(j_curr, self.spikes, self.inh_weights, self.R_m, self.inh_R)
        self.current = j_curr # None ## store electrical current
        self.surrogate = apply_surrogate_dfx(j_curr, c1=0.82, c2=0.08)
        self.voltage, self.spikes, self.threshold, self.refract = \
            run_cell(dt, j_curr, self.voltage, self.threshold, self.tau_m,
                     self.refract, self.refract_T, self.thrGain, self.thrLeak,
                     self.rho_b, sticky_spikes=self.sticky_spikes, v_min=self.v_min)
        ## update tols
        self.timeOfLastSpike = update_times(t, self.spikes, self.timeOfLastSpike)
        #self.timeOfLastSpike = (1 - self.spikes) * self.timeOfLastSpike + (self.spikes * t)

    def reset(self, **kwargs):
        self.voltage = jnp.zeros((self.batch_size, self.n_units))
        self.refract = jnp.zeros((self.batch_size, self.n_units)) + self.refract_T
        self.current = None
        self.surrogate = None
        self.timeOfLastSpike = jnp.zeros((self.batch_size, self.n_units))
        self.spikes = jnp.zeros((self.batch_size, self.n_units)) #None
        if self.thr_persist == False: ## if thresh non-persistent, reset to base value
            self.threshold = self.threshold0 + 0

    def save(self, directory, **kwargs):
        file_name = directory + "/" + self.name + ".npz"
        if self.thr_persist == False:
            jnp.savez(file_name, threshold=self.threshold0)
        else:
            jnp.savez(file_name, threshold=self.threshold)

    def load(self, directory, **kwargs):
        file_name = directory + "/" + self.name + ".npz"
        data = jnp.load(file_name)
        self.threshold = data['threshold']
        self.threshold0 = self.threshold + 0
