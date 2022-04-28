import os
import sys, getopt, optparse
import pickle
sys.path.insert(0, '../')
import tensorflow as tf
import numpy as np
import time

# import general simulation utilities
from ngclearn.utils.config import Config
import ngclearn.utils.transform_utils as transform
import ngclearn.utils.metric_utils as metric
import ngclearn.utils.io_utils as io_tools
from ngclearn.utils.data_utils import DataLoader

seed = 69
os.environ["CUDA_VISIBLE_DEVICES"]="0"
tf.random.set_seed(seed=seed)
np.random.seed(seed)

"""
################################################################################
Demo/Tutorial #3 File:
Evaluates a trained NGC classifier on the MNIST database test-set.

Usage:
$ python eval_train.py --config=/path/to/file.cfg --gpu_id=0

@author Alexander Ororbia
################################################################################
"""

# read in configuration file and extract necessary simulation variables/constants
options, remainder = getopt.getopt(sys.argv[1:], '', ["config=","gpu_id="])
# GPU arguments
cfg_fname = None
use_gpu = False
gpu_id = -1
for opt, arg in options:
    if opt in ("--config"):
        cfg_fname = arg.strip()
    elif opt in ("--gpu_id"):
        gpu_id = int(arg.strip())
        use_gpu = True
mid = gpu_id
if mid >= 0:
    print(" > Using GPU ID {0}".format(mid))
    os.environ["CUDA_VISIBLE_DEVICES"]="{0}".format(mid)
    #gpu_tag = '/GPU:0'
    gpu_tag = '/GPU:0'
else:
    os.environ["CUDA_VISIBLE_DEVICES"]="-1"
    gpu_tag = '/CPU:0'

save_marker = 1

args = Config(cfg_fname)

model_fname = args.getArg("model_fname")
dev_batch_size = int(args.getArg("dev_batch_size")) #128 #32

# create development/validation sample
xfname = args.getArg("test_xfname")
yfname = args.getArg("test_yfname")
print(" Evaluating model on X.fname = {}".format(xfname))
print("                     Y.fname = {}".format(yfname))
X = ( tf.cast(np.load(xfname),dtype=tf.float32) ).numpy()
Y = ( tf.cast(np.load(yfname),dtype=tf.float32) ).numpy()
dev_set = DataLoader(design_matrices=[("z3",X),("z0",Y)], batch_size=dev_batch_size, disable_shuffle=True)

def eval_model(agent, dataset, calc_ToD, verbose=False):
    """
        Evaluates performance of agent on this fixed-point data sample
    """
    ToD = 0.0 # total disrepancy over entire data pool
    Ly = 0.0 # metric/loss over entire data pool
    Acc = 0.0
    N = 0.0 # number samples seen so far
    for batch in dataset:
        x_name, x = batch[0]
        y_name, y = batch[1]
        N += x.shape[0]
        #y_hat = agent.settle(x, y) # conduct iterative inference
        y_hat = agent.predict(x)

        # update tracked fixed-point losses
        Ly = tf.reduce_sum( metric.cat_nll(tf.nn.softmax(y_hat), y) ) + Ly

        # track raw accuracy
        y_ind = tf.cast(tf.argmax(y,1),dtype=tf.int32)
        y_pred = tf.cast(tf.argmax(y_hat,1),dtype=tf.int32)
        comp = tf.cast(tf.equal(y_pred,y_ind),dtype=tf.float32)
        Acc += tf.reduce_sum( comp )

        agent.clear()
        if verbose == True:
            print("\r Acc {0}  Ly {1} over {2} samples...".format((Acc/(N * 1.0)), (Ly/(N * 1.0)), N),end="")
    if verbose == True:
        print()
    Ly = Ly / N
    Acc = Acc / N
    return ToD, Ly, Acc

################################################################################
# Start simulation
################################################################################
with tf.device(gpu_tag):
    def calc_ToD(agent):
        """Measures the total discrepancy (ToD) of a given NGC model"""
        ToD = 0.0
        L2 = agent.ngc_model.extract(node_name="e2", node_var_name="L")
        L1 = agent.ngc_model.extract(node_name="e1", node_var_name="L")
        L0 = agent.ngc_model.extract(node_name="e0", node_var_name="L")
        ToD = -(L0 + L1 + L2)
        return ToD

    agent = io_tools.deserialize(model_fname)
    print(" > Loading model: ",model_fname)

    ############################################################################
    vToD, vLy, vAcc = eval_model(agent, dev_set, calc_ToD, verbose=True)
    print(" Ly = {}  Acc = {}".format(vLy, vAcc))
