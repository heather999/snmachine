"""
Module for extracting and saving GPs
"""

import os
import pickle
import subprocess
import sys
import time

import george
import numpy as np
import pandas as pd
import scipy
import scipy.optimize as op
import sncosmo

from astropy.table import Table, vstack, hstack, join
from functools import partial
from multiprocessing import Pool
from scipy import interpolate
from scipy.interpolate import interp1d
from snmachine import sndata
from snmachine import reducedchisquared as rcs

try:
    import george
    has_george=True
except ImportError:
    has_george=False

try:
    from gapp import dgp
    has_gapp=True
except ImportError:
    has_gapp=False


def compute_gps(dataset, number_gp, t_min, t_max, kernel_param=[500., 20.], output_root=None, number_processes=1, gp_algo='george'):
    """Runs Gaussian process code on entire dataset. The result is stored inside the models attribute of the dataset object.

    The result is also saved on external files, if an output root is given.

    Parameters
    ----------
    dataset : Dataset object (sndata class)
        Dataset.
    number_gp : int
        Number of points to evaluate the Gaussian Process Regression at.
    t_min : float
        Minimim time to evaluate the Gaussian Process Regression at.
    t_max : float
        Maximum time to evaluate the Gaussian Process Regression at.
    kernel_param : list-like, optional
        Initial values for kernel parameters. These should be roughly the scale length in the y & x directions.
    output_root : {None, str}, optional
        If None, don't save anything. If str, it is the output directory, so save the flux and error estimates and used kernels there.
    number_processes : int, optional
        Number of processors to use for parallelisation (shared memory only). By default `nprocesses` = 1.
    gp_algo : str, optional
        which gp package is used for the Gaussian Process Regression, GaPP or george
    """
    print ('Performing Gaussian process regression')
    initial_time = time.time()

    # Check for parallelisation
    if number_processes == 1: # non parallelizing
        _compute_gps_single_core(dataset, number_gp, t_min, t_max, kernel_param, output_root, gp_algo)

    else: # parallelizing
        _compute_gps_parallel(dataset, number_gp, t_min, t_max, kernel_param, output_root, number_processes, gp_algo)

    print ('Time taken for Gaussian process regression', time.time()-initial_time)


def read_gp_files_into_models(dataset, path_saved_gp_files):
    """Reads the saved files into the dataset models so we can start from a previously saved Gaussian Processes.

    The files can have been stored as `.ascii` or `.fits`.

    Parameters
    ----------
    dataset : Dataset object (sndata class)
        Dataset.
    path_saved_gp_files : str
        Path for the Gaussian Process curve files.
    """
    print ('Restarting from stored Gaussian Processes...')
    for obj in dataset.object_names:
            obj_saved_gps_file = os.path.join(path_saved_gp_files, 'gp_'+obj)
            try:
                obj_saved_gps = Table.read(obj_saved_gps_file, format='ascii')
            except:
                try:
                    obj_saved_gps = Table.read(obj_saved_gps_file, format='fits')
                except IOError:
                    print ('IOError, file ', obj_saved_gps_file, 'does not exist.')
            dataset.models[obj] = obj_saved_gps


def _compute_gps_single_core(dataset, number_gp, t_min, t_max, kernel_param, output_root, gp_algo):
    """Computes the Gaussian process code on entire dataset in a single core.

    Parameters
    ----------
    dataset : Dataset object (sndata class)
        Dataset.
    number_gp : int
        Number of points to evaluate the Gaussian Process Regression at.
    t_min : float
        Minimim time to evaluate the Gaussian Process Regression at.
    t_max : float
        Maximum time to evaluate the Gaussian Process Regression at.
    kernel_param : list-like, optional
        Initial values for kernel parameters. These should be roughly the scale length in the y & x directions.
    output_root : {None, str}, optional
        If None, don't save anything. If str, it is the output directory, so save the flux and error estimates and used kernels there.
    gp_algo : str, optional
        which gp package is used for the Gaussian Process Regression, GaPP or george
    """
    for i in range(len(dataset.object_names)):
        obj = dataset.object_names[i]
        try:
            output = _compute_gp_all_passbands(obj, dataset, number_gp, t_min, t_max, kernel_param,
                                                    output_root=output_root, gp_algo=gp_algo)
            dataset.models[obj] = output
        except ValueError:
            print('Object {} has fallen over!'.format(obj))
    print('Models fitted with the GP values.')


def _compute_gps_parallel(dataset, number_gp, t_min, t_max, kernel_param, output_root, number_processes, gp_algo):
    """Computes the Gaussian process code on entire dataset in a parallel way.

    Parameters
    ----------
    dataset : Dataset object (sndata class)
        Dataset.
    number_gp : int
        Number of points to evaluate the Gaussian Process Regression at.
    t_min : float
        Minimim time to evaluate the Gaussian Process Regression at.
    t_max : float
        Maximum time to evaluate the Gaussian Process Regression at.
    kernel_param : list-like, optional
        Initial values for kernel parameters. These should be roughly the scale length in the y & x directions.
    output_root : {None, str}, optional
        If None, don't save anything. If str, it is the output directory, so save the flux and error estimates and used kernels there.
    number_processes : int, optional
        Number of processors to use for parallelisation (shared memory only). By default `nprocesses` = 1.
    gp_algo : str, optional
        which gp package is used for the Gaussian Process Regression, GaPP or george
    """
    p = Pool(number_processes, maxtasksperchild=10)

    #Pool and map can only really work with single-valued functions
    partial_gp = partial(_compute_gp_all_passbands, dataset=dataset, number_gp=number_gp, t_min=t_min, t_max=t_max, kernel_param=kernel_param,
                         output_root=output_root, gp_algo=gp_algo)

    out = p.map(partial_gp, dataset.object_names, chunksize=10)
    p.close()

    out = np.reshape(out,(len(dataset.object_names),3))
    for i in range(len(out)):
        obj = dataset.object_names[i]
        dataset.models[obj] = out[i,0]
    print('Models fitted with the GP values.')


def _compute_gp_all_passbands(obj, dataset, number_gp, t_min, t_max, kernel_param, output_root=None, gp_algo='george'):
    """Compute/ Fit a Gaussian process curve in every passband of an object.

    If asked to save the output, it saves the Gaussian process curve in every passband of an object and the GP instances and kernel used.

    Parameters
    ----------
    obj : str
        Name of the object.
    dataset : Dataset object (sndata class)
        Dataset.
    number_gp : int
        Number of points to evaluate the Gaussian Process Regression at.
    t_min : float
        Minimim time to evaluate the Gaussian Process Regression at.
    t_max : float
        Maximum time to evaluate the Gaussian Process Regression at.
    kernel_param : list-like
        Initial values for kernel parameters. These should be roughly the scale length in the y & x directions.
    output_root : {None, str}, optional
        If None, don't save anything. If str, it is the output directory, so save the flux and error estimates and used kernels there.
    gp_algo : str
        which gp package is used for the Gaussian Process Regression, GaPP or george

    Returns
    -------
    obj_gps : astropy.table.Table
        Table with evaluated Gaussian process curve and errors at each passband.
    used_gp_dict : dict
        Dictionary whose key is the passband and whose value is the Gaussian Process Regression instance that fitted that passband.
    used_kernels_dict: dict
        Dictionary whose key is the passband and whose value is the id of the kernel used in the GP that fitted that passband.
    """

    if gp_algo=='gapp' and not has_gapp:
        print('No GP module gapp. Defaulting to george instead.')
        gp_algo='george'
    obj_data = dataset.data[obj] # object's lightcurve
    obj_data = rcs.rename_passband_column(obj_data.to_pandas())
    unique_passbands = np.unique(obj_data.passband)
    gp_times = np.linspace(t_min, t_max, number_gp)

    # Store the output in another astropy table
    obj_gps = []
    used_gp_dict = {}
    used_kernels_dict = {}
    filter_set = np.asarray(dataset.filter_set)
    for pb in filter_set:
        used_kernels_dict[pb] = None # inilialize None kernel to each passband
        if pb in unique_passbands:
            obj_data_pb = obj_data.loc[obj_data.passband == pb] # the observations in this passband

            if gp_algo=='gapp':
                gp         = dgp.DGaussianProcess(obj_data_pb.mjd, obj_data_pb.flux, obj_data_pb.flux_error, cXstar=(t_min, t_max, number_gp))
                obj_gp_pb_array, theta = gp.gp(theta=kernel_param)

            elif gp_algo=='george':
                gp_obs, gp, chosen_kernel = fit_best_gp(kernel_param, obj_data_pb, gp_times)

                mu, std = gp_obs.flux.values, gp_obs.flux_error.values
                obj_gp_pb_array = np.column_stack((gp_times, mu, std)) # stack the GP results in a array momentarily
                used_kernels_dict[pb] = chosen_kernel
            used_gp_dict[pb] = gp
        else:
            obj_gp_pb_array = np.zeros([number_gp, 3])
        obj_gp_pb = Table([obj_gp_pb_array[:, 0], obj_gp_pb_array[:, 1], obj_gp_pb_array[:, 2], [pb]*number_gp],
                           names=['mjd', 'flux', 'flux_error', 'filter'])
        if len(obj_gps) == 0: # this is the first passband so we initialize the table
            obj_gps = obj_gp_pb
        else:
            obj_gps = vstack((obj_gps, obj_gp_pb))

    if output_root != None:
        obj_gps.write(os.path.join(output_root, 'gp_'+obj), format='fits', overwrite=True)
        with open(os.path.join(output_root, 'used_gp_dict_'+obj+'.pckl'), 'wb') as f:
            pickle.dump(used_gp_dict, f, pickle.HIGHEST_PROTOCOL)
        with open(os.path.join(output_root, 'used_kernels_dict_'+obj+'.pckl'), 'wb') as f:
            pickle.dump(used_kernels_dict, f, pickle.HIGHEST_PROTOCOL)

    return obj_gps


def fit_best_gp(kernel_param, obj_data, gp_times):
    """Fits Gaussian processes in a hierarchical way until it finds one whose reduced X^2 < 1 or tried all.

    Parameters
    ----------
    kernel_param : list-like
        Initial values for kernel parameters. These should be roughly the scale length in the y & x directions.
    obj_data : pandas.core.frame.DataFrame
        Time, flux and flux error of the data (specific filter of an object).
    gp_times :
        Times to evaluate the Gaussian Process at.

    Returns
    -------
    gp_obs : pandas.core.frame.DataFrame
        Time, flux and flux error of the fitted Gaussian Process.
    gp : george.gp.GP
        The GP instance that was used to fit the object.
    kernel_id : str
        The id of the kernel chosen. This can then be used as a feature.
    """
    possible_kernel_ids = ['kernel 0', 'kernel 1', 'kernel 2', 'kernel 3', 'kernel 4']
    possible_kernel_names = ['ExpSquared', 'ExpSquared', 'ExpSquared', 'ExpSquared+ExpSine2', 'ExpSquared+ExpSine2']
    possible_kernel_params = [[kernel_param[0]**2, kernel_param[1]**2, None, None, None, None, None],
                              [400., 200., None, None, None, None, None],
                              [400., 20., None, None, None, None, None],
                              [400., 20., 2., 4., 4., 6., 6.],
                              [19., 9., 2., 4., 4., 6., 6.]]
    number_diff_kernels = len(possible_kernel_names)
    i = 0 # initializing the while loop
    all_obj_gp = number_diff_kernels*[''] # initializing
    all_gp_instances = number_diff_kernels*[''] # initializing
    all_reduced_chi_squared = np.zeros(number_diff_kernels) + 666 # just a random number > 1 to initialize
    while i < number_diff_kernels and all_reduced_chi_squared[i-1] > 1 :
        obj_gp, gp_instance = fit_gp(kernel_name=possible_kernel_names[i], kernel_param=possible_kernel_params[i], obj_data=obj_data, gp_times=gp_times)
        reduced_chi_squared = rcs.compute_reduced_chi_squared(obj_data, obj_gp)
        kernel_id = possible_kernel_ids[i]
        all_obj_gp[i] = obj_gp
        all_gp_instances[i] = gp_instance
        all_reduced_chi_squared[i] = reduced_chi_squared
        i += 1
        if i == number_diff_kernels and reduced_chi_squared > 1: # all kernels/parameters are bad for this object
            obj_gp, gp_instance, reduced_chi_squared, kernel_id = _choose_less_bad_kernel(all_obj_gp, all_gp_instances, all_reduced_chi_squared,
                                                                                          possible_kernel_ids)
    return obj_gp, gp_instance, kernel_id


def _choose_less_bad_kernel(all_obj_gp, all_gp_instances, all_reduced_chi_squared, possible_kernel_ids):
    """If all kernels give a bad reduced X^2, choose the less bad of them.

    Parameters
    ----------
    all_obj_gp : list-like
        List of the DataFrames containing time, flux and flux error for each GP instance in `all_gp_instances`.
    all_gp_instances : list-like
        List of all GP instances used to fit the object.
    all_reduced_chi_squared : list-like
        List of the reduced X^2 values for each GP instance in `all_gp_instances`.
    possible_kernel_ids : list-like
        List of all the possible kernel ids.

    Returns
    -------
    less_bad_obj_gp : pandas.core.frame.DataFrame
        Time, flux and flux error of the fitted Gaussian Process `gp`.
    less_bad_gp_instance : george.gp.GP
        The GP instance that was used to fit the object. It is the one that gives the lower reduced X^2.
    less_bad_reduced_chi_squared : float
        The reduced X^2 given by the Gaussian Process `gp`.
    """
    index_min_reduced_chi_squared = np.argmin(all_reduced_chi_squared)
    less_bad_obj_gp = all_obj_gp[index_min_reduced_chi_squared]
    less_bad_gp_instance = all_gp_instances[index_min_reduced_chi_squared]
    less_bad_reduced_chi_squared = all_reduced_chi_squared[index_min_reduced_chi_squared]
    less_bad_kernel = possible_kernel_ids[index_min_reduced_chi_squared]
    return less_bad_obj_gp, less_bad_gp_instance, less_bad_reduced_chi_squared, less_bad_kernel


def fit_gp(kernel_name, kernel_param, obj_data, gp_times):
    """Fit a Gaussian process curve at specific evenly spaced points along a light curve.

    Parameters
    ----------
    kernel_name : str
        The kernel to fit the data. It can be ExpSquared or ExpSquared+ExpSine2.
    kernel_param : list-like
        Initial values for kernel parameters. These should be roughly the scale length in the y & x directions.
    obj_data : pandas.core.frame.DataFrame
        Time, flux and flux error of the data (specific filter of an object).
    gp_times :
        Times to evaluate the Gaussian Process at.

    Returns
    -------
    obj_gp : pandas.core.frame.DataFrame
        Time, flux and flux error of the fitted Gaussian Process.
    gp : george.gp.GP
        The GP instance that was used to fit the object.
    """
    obj_times = obj_data.mjd
    obj_flux = obj_data.flux
    obj_flux_error = obj_data.flux_error

    def neg_log_like(p): # Objective function: negative log-likelihood
        gp.set_parameter_vector(p)
        loglike = gp.log_likelihood(obj_flux, quiet=True)
        return -loglike if np.isfinite(loglike) else 1e25

    def grad_neg_log_like(p): # Gradient of the objective function.
        gp.set_parameter_vector(p)
        return -gp.grad_log_likelihood(obj_flux, quiet=True)

    kernel = get_kernel(kernel_name, kernel_param)

    gp = george.GP(kernel)
    gp.compute(obj_times, obj_flux_error)
    results = op.minimize(neg_log_like, gp.get_parameter_vector(), jac=grad_neg_log_like,
                          method="L-BFGS-B", tol=1e-6)

    if np.sum(np.isnan(results.x)) != 0 : # the minimiser reaches a local minimum
        kernel_param[4] = kernel_param[4]+.1 # change a bit initial conditions so we don't go to that minima
        kernel = get_kernel(kernel_name, kernel_param)
        gp = george.GP(kernel)
        gp.compute(obj_times, obj_flux_error)
        results = op.minimize(neg_log_like, gp.get_parameter_vector(), jac=grad_neg_log_like,
                          method="L-BFGS-B", tol=1e-6)

    gp.set_parameter_vector(results.x)
    gp_mean, gp_cov = gp.predict(obj_flux, gp_times)
    obj_gp = pd.DataFrame(columns=['mjd'], data=gp_times)
    obj_gp['flux']  = gp_mean
    if np.sum(np.diag(gp_cov)<0) == 0:
        obj_gp['flux_error'] = np.sqrt(np.diag(gp_cov))
    else: # do not choose this kernel
        obj_gp['flux_error'] = 666666
    return obj_gp, gp


def get_kernel(kernel_name, kernel_param):
    """Get the chosen kernel with the given initial conditions.

    Parameters
    ----------
    kernel_name : str
        The kernel to fit the data. It can be ExpSquared or ExpSquared+ExpSine2.
    kernel_param : list-like
        Initial values for kernel parameters.

    Returns
    -------
    kernel : george.kernels
        The kernel instance to be used in the Gaussian Process.

    Raises
    ------
    AttributeError
        The only available kernels are 'ExpSquared' and 'ExpSquared+ExpSine2'.
    """
    if kernel_name not in ['ExpSquared', 'ExpSquared+ExpSine2']:
        raise AttributeError("The only available kernels are 'ExpSquared' and 'ExpSquared+ExpSine2'.")
    kExpSquared = kernel_param[0]*george.kernels.ExpSquaredKernel(metric=kernel_param[1])
    if kernel_name == 'ExpSquared':
        kernel = kExpSquared
    elif kernel_name == 'ExpSquared+ExpSine2':
        kExpSine2 = kernel_param[4]*george.kernels.ExpSine2Kernel(gamma=kernel_param[5],log_period=kernel_param[6])
        kernel = kExpSquared + kExpSine2
    return kernel