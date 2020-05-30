# hierCC.py
# hierarchical Clustering Complex of MLST allelic profiles
#
# Author: Zhemin Zhou
# Lisence: GPLv3
#
# New assignment: hierCC.py -p <allelic_profile> -o <output_prefix>
# Incremental assignment: hierCC.py -p <allelic_profile> -o <output_prefix> -i <old_cluster_npy>
# Input format:
# ST_id gene1 gene2
# 1 1 1
# 2 1 2
# ...

import os, sys, time, argparse
import pandas as pd, numpy as np, numba as nb
from multiprocessing import Pool

try:
    from configure import uopen, logger, xrange
except:
    from .configure import uopen, logger, xrange


@nb.jit(nopython=True)
def assignment2(dists, res):
    for id in xrange(len(dists)):
        idx, ref, jd = dists[id]
        res[idx, jd+1:] = res[ref, jd+1:]


def get_distance(idx):
    global mat
    n_loci = mat.shape[1] - 1
    if idx == 0 or idx >= mat.shape[0]:
        return np.zeros(shape=[0, 3], dtype=int)
    profile = mat[idx]
    s = np.sum((profile[1:] == mat[:idx, 1:]) & (profile[1:] > 0), 1)
    ql = np.sum(profile[1:] > 0)
    rl = np.sum(mat[:idx, 1:] > 0, 1)
    rll = n_loci - np.max([(n_loci - ql) * 3, int((n_loci - ql) + n_loci * 0.03 + 0.5)])
    rl[rl < rll] = rll
    rl[rl > ql] = ql
    rl[rl < 0.5] = 0.5
    d = ((rl - s).astype(float) * n_loci / rl + 0.5).astype(int)
    dists = np.vstack([np.repeat(idx, idx), np.arange(idx), d]).astype(int).T
    return dists[np.argsort(dists.T[2], kind='mergesort')]

def get_distance2(idx):
    global mat
    n_loci = mat.shape[1] - 1
    if idx == 0 or idx >= mat.shape[0]:
        return np.zeros(shape=[0, 4], dtype=int)
    profile = mat[idx]
    ql = np.max([1.0, np.sum(profile[1:] > 0).astype(float)])
    d1 = (n_loci * np.sum((profile[1:] != mat[:idx, 1:]) & (profile[1:] > 0), 1) / ql + 0.5).astype(int) + 1
    d2 = n_loci - np.sum((profile[1:] == mat[:idx, 1:]) & (profile[1:] > 0), 1) + 1
    d1[d1 > d2] = d2[d1 > d2]
    dists = np.vstack([np.repeat(idx, idx), np.arange(idx), d1, d2]).astype(int).T
    return dists[np.argsort(dists.T[2], kind='mergesort')]


@nb.jit(nopython=True)
def assignment(dists, res):
    for id in xrange(len(dists)):
        idx, ref, d1 = dists[id]
        for d in xrange(d1+1, n_loci + 1):
            if res[idx, d] != res[ref, d]:
                if d >= res[idx, 0]:
                    if res[idx, d] < res[ref, d]:
                        grps = [res[idx, d], res[ref, d]]
                    else:
                        grps = [res[ref, d], res[idx, d]]
                    res[:idx, d][res[:idx, d] == grps[1]] = grps[0]
                    res[idx, d] = grps[0]
                else:
                    if res[idx, d] < res[ref, d]:
                        res[:idx, d][res[:idx, d] == res[ref, d]] = res[idx, d]
                    else:
                        res[idx, d:] = res[ref, d:]
                        break
            else:
                break
        if res[idx, 0] > d1+1:
            res[idx, 0] = d1+1
    return


def get_args(args):
    parser = argparse.ArgumentParser(description='''hierCC takes allelic profile (as in https://pubmlst.org/data/) and
work out specialised single linkage clustering result of all the profiles in the list.''',
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('-p', '--profile', help='[INPUT; REQUIRED] name of the profile file. Can be GZIPed.',
                        required=True)
    parser.add_argument('-o', '--output',
                        help='[OUTPUT; REQUIRED] Prefix for the output files. These include a NUMPY and TEXT verions of the same clustering result',
                        required=True)
    parser.add_argument('-i', '--incremental', help='[INPUT; optional] The NUMPY version of an old clustering result',
                        default='')
    parser.add_argument('-d', '--delta',
                        help='[optional] comma delimited list of threshold (delta). All values are included by default.',
                        default=None)
    parser.add_argument('--immutable',
                        help='[optional] Use a immutable clustering system. The designations of old profiles are immutable. Faster but leads to non-optimal assignment.',
                        default=False, action='store_true')

    return parser.parse_args(args)


def hierCC(args):
    params = get_args(args)
    ot = time.time()
    profile_file, cluster_file, old_cluster = params.profile, params.output + '.npz', params.incremental

    global mat, n_loci
    mat = pd.read_csv(profile_file, sep='\t', header=None, dtype=str).values
    allele_columns = np.array([i == 0 or (not h.startswith('#')) for i, h in enumerate(mat[0])])
    mat = mat[1:, allele_columns].astype(int)
    n_loci = mat.shape[1] - 1

    logger(
        '{0}: Loaded in allelic profiles with dimension: {1} and {2}. The first column is assumed to be type id.'.format(
            time.time() - ot, *mat.shape))
    if not params.immutable:
        absence = np.sum(mat <= 0, 1)
        mat = mat[np.argsort(absence, kind='mergesort')]

    if os.path.isfile(old_cluster):
        od = np.load(old_cluster, allow_pickle=True)
        cls = od['hierCC']

        typed = {c[0]: id for id, c in enumerate(cls) if c[0] > 0}
        if len(typed) > 0:
            logger('{0}: Loaded in {1} old hierCC assignments.'.format(time.time() - ot, len(typed)))
            mat_idx = np.array([t in typed for t in mat.T[0]])
            mat[:] = np.vstack([mat[mat_idx], mat[(mat_idx) == False]])
    else:
        typed = {}

    logger('{0}: Start hierCC assignments'.format(time.time() - ot))
    pool = Pool(10)

    res = np.repeat(mat.T[0], mat.shape[1]+1).reshape(mat.shape)
    res[1:, 0] = n_loci + 1
    for index in xrange(0, mat.shape[0], 100):
        to_run = []
        for idx in np.arange(index, index + 100):
            if idx < mat.shape[0]:
                if mat[idx, 0] in typed:
                    res[idx, :] = cls[typed[mat[idx, 0]], :]
                else:
                    to_run.append(idx)
        if len(to_run) == 0:
            continue
        if not params.immutable:
            dists = np.vstack(pool.map(get_distance, to_run))
            assignment(dists, res)
        else:
            dists = np.vstack([r[0] for r in pool.map(get_distance, to_run)])
            assignment2(dists, res)

        logger('{0}: Assigned {1} of {2} types into hierCC.'.format(time.time() - ot, index, mat.shape[0]))
    res.T[0] = mat.T[0]
    np.savez_compressed(cluster_file, hierCC=res)

    if not params.delta:
        with uopen(params.output + '.hierCC.gz', 'w') as fout:
            fout.write('#ST_id\t{0}\n'.format('\t'.join(['d' + str(id) for id in np.arange(n_loci)])))
            for r in res[np.argsort(res.T[0])]:
                fout.write('\t'.join([str(rr) for rr in r]) + '\n')
    else:
        deltas = map(int, params.delta.split(','))
        with uopen(params.output + '.hierCC.gz', 'w') as fout:
            fout.write('#ST_id\t{0}\n'.format('\t'.join(['d' + str(id) for id in deltas])))
            for r in res[np.argsort(res.T[0])]:
                fout.write('\t'.join([str(r[id + 1]) for id in deltas]) + '\n')
    del res
    logger('NUMPY clustering result (for incremental hierCC): {0}.npz'.format(params.output))
    logger('TEXT  clustering result (for visual inspection): {0}.hierCC.gz'.format(params.output))


mat, n_loci = None, None
if __name__ == '__main__':
    hierCC(sys.argv[1:])
