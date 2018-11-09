from __future__ import division
from snmachine import sndata, snfeatures, snclassifier, tsne_plot, example_data
from sklearn.externals import joblib
from argparse import ArgumentParser
import random
import numpy as np
import matplotlib.pyplot as plt
import time, os, pywt, subprocess
from sklearn.decomposition import PCA
from astropy.table import Table,join,vstack
from astropy.io import fits,ascii
import sklearn.metrics
import sncosmo
import yaml

def process():
    parser = ArgumentParser(description="Configuration for cadence comparisons")

    parser.add_argument('--config', '-c')
    parser.add_argument('--jobid', '-j', type=int)

    arguments = parser.parse_args()
    return vars(arguments)

if __name__ == "__main__":

    args = process()
    jobid = args['jobid']

    try:
        with open(args['config']) as f:
            params = yaml.load(f)
    except IOError:
        print("Invalid yaml file provided")
        exit()

    print("The JOBID is:\n {}".format(jobid))
    print("The PARAMS are:\n {}".format(params))

    dataset = params.get("dataset", None)
    print("The dataset is:\n{}".format(dataset))

    rt = params.get("dataroot", None)
    print("The dataroot is:\n{}".format(rt))

    prefix_Ia = params.get("prefix_Ia", None)
    print("The data prefix for Ia's is:\n{}".format(prefix_Ia))

    prefix_NONIa = params.get("prefix_NONIa", None)
    print("The data prefix for NON Ia's is:\n{}".format(prefix_NONIa))

    final_outdir="/share/hypatia/snmachine_resources/data/LSST_Cadence_WhitePaperClassResults/output_data/output_{}_no_z/".format(dataset)
    # final_outdir=os.path.join('output_data', 'output_%s_no_z' %dataset,'')

    ## READ IN ENTIRE DATASET
    if 'wfd' in dataset:
        dat=sndata.LSSTCadenceSimulations(rt, prefix_Ia, prefix_NONIa, indices=range(1,2))
    else:
        dat=sndata.LSSTCadenceSimulations(rt, prefix_Ia, prefix_NONIa, indices=range(1,21))
    # dat=sndata.LSSTCadenceSimulations(rt, prefix_Ia, prefix_NONIa)

    types=dat.get_types()
    print(type(types))

    # If we write this table to file and inpsect the format of supernova types we find there are 6 variants:
    ascii.write(types, '{}{}_types.csv'.format(final_outdir, dataset), format='csv', fast_writer=True)
    LIST_TYPES = "awk -F ',' '{{print $2}}' {}{}_types.csv | sort | uniq -c".format(final_outdir, dataset)
    subprocess.call(LIST_TYPES, shell=True)

    # Like for SPCC example notebook where we restrict ourselves to three supernova types:
    # Ia (1), II (2) and Ibc (3) by carrying out the following pre-proccessing steps
    # types['Type'] = types['Type']-100

    # types['Type'][np.floor(types['Type']/10)==2]=2
    # types['Type'][np.floor(types['Type']/10)==3]=3
    # types['Type'][np.floor(types['Type']/10)==4]=4

    # print(types)
    # ascii.write(types, '{}{}_types_normalised.csv'.format(final_outdir, dataset), format='csv', fast_writer=True)

    # Get object names from data
    training_objects = dat.object_names[:]
    # Turn this into a Python list
    training_set = list(training_objects)
    print(type(training_set)) # Should be a list

    # Randomly select 2000 objects from this Python list of objects
    training_set = random.sample(training_set, 10000)

    # RESTART FROM WAVELETS
    # Copy int to finaldir and read in raw wavelets
    wavelet_feats=snfeatures.WaveletFeatures(wavelet='sym2', ngp=100)
    wave_raw, wave_err=wavelet_feats.restart_from_wavelets(dat, os.path.join(final_outdir, 'int', ''))
    wavelet_features,vals,vec,means=wavelet_feats.extract_pca(dat.object_names.copy(), wave_raw)

    # RESTART FROM GPs
    # TODO:
    # - Have a flag to choose whether to restart from GPs or Wavelts
    # wave_features=waveFeats.extract_features(dat,nprocesses=6,output_root=out_int,save_output='all')
    # wave_features.write('{}_wavelets_rank_{}.dat'.format(run_name, rank), format='ascii')
    # np.savetxt('%s_wavelets_PCA_vals.dat' %run_name,waveFeats.PCA_eigenvals)
    # np.savetxt('%s_wavelets_PCA_vec.dat' %run_name,waveFeats.PCA_eigenvectors)
    # np.savetxt('%s_wavelets_PCA_mean.dat' %run_name,waveFeats.PCA_mean)
    # PCA_vals=waveFeats.PCA_eigenvals
    # PCA_vec=waveFeats.PCA_eigenvectors
    # PCA_mean=waveFeats.PCA_mean

    ## CLASSIFY
    classifiers = params.get("classifiers", None)
    print("Classifiers :\n{}".format(classifiers))

    nproc=1

    # Print available classifiers
    print(snclassifier.choice_of_classifiers)

    # ### Wavelet features
    out_class=os.path.join(final_outdir, 'classifications_{}'.format(jobid), '')
    # out_plots=os.path.join(out_class, 'plots', '')
    try:
        subprocess.call(['mkdir',out_class])
        # subprocess.call(['mkdir',out_plots])
    except IOError:
        print("Already exists, use another name...")
        exit()


    # plt.figure(2)
    # plt.figure(figsize=(12,6))
    print("Using {} SNe for training".format(len(training_set)))
    print(training_set)

    pc = 0.7

    # print("Working on :\n {}".format(training_set))
    clss=snclassifier.run_pipeline(wavelet_features,types,output_name=os.path.join(out_class,'wavelets'),
                              classifiers=classifiers, nprocesses=nproc,
                              plot_roc_curve=False, return_classifier=True)

    # training_ratio = int(training_set*1000)
    # training_ratio = str(training_ratio)
    # joblib.dump(clss, '{}{}_{}_model.pkl'.format(out_class, dataset, len(training_set)))
    joblib.dump(clss, '{}{}_{}_model.pkl'.format(out_class, dataset, pc))

    exit()