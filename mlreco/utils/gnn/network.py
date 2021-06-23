# Defines network incidence matrices
import numpy as np
import numba as nb
import torch

from scipy.spatial import Delaunay
from scipy.sparse.csgraph import minimum_spanning_tree

from mlreco.utils.numba import numba_wrapper, submatrix_nb, cdist_nb


@nb.njit
def loop_graph(n: nb.int64) -> nb.int64[:,:]:
    """
    Function that returns an incidence matrix of a graph that only
    connects nodes with themselves.

    Args:
        n (int): Number of nodes C
    Returns:
        np.ndarray: (2,C) Tensor of edges
    """
    # Create the incidence matrix
    ret = np.empty((2,n), dtype=np.int64)
    for k in range(n):
        ret[k] = [k,k]

    return ret.T


@nb.njit
def complete_graph(batch_ids: nb.int64[:],
                   dist_mat: nb.float64[:,:] = None,
                   max_dist: nb.float64 = -1.) -> nb.int64[:,:]:
    """
    Function that returns an incidence matrix of a complete graph
    that connects every node with ever other node.

    Args:
        batch_ids (np.ndarray): (C) List of batch ids
        dist_mat (np.ndarray) : (C,C) Tensor of pair-wise cluster distances
        max_dist (double)     : Maximal edge length
    Returns:
        np.ndarray: (2,E) Tensor of edges
    """
    # Count the number of edges in the graph
    edge_count = 0
    for b in np.unique(batch_ids):
        edge_count += np.sum(batch_ids == b)**2
    edge_count = int((edge_count-len(batch_ids))/2)
    if not edge_count:
        return np.empty((2,0), dtype=np.int64)

    # Create the sparse incidence matrix
    ret = np.empty((edge_count,2), dtype=np.int64)
    k = 0
    for i in range(len(batch_ids)):
        for j in range(i+1, len(batch_ids)):
            if batch_ids[i] == batch_ids[j]:
                ret[k] = [i,j]
                k += 1

    # If requested, remove the edges above a certain length threshold
    if max_dist > -1:
        assert dist_mat is not None
        dists = np.empty(len(ret), dtype=dist_mat.dtype)
        for k, e in enumerate(ret):
            dists[k] = dist_mat[e[0],e[1]]
        ret = ret[dists < max_dist]

    # Add the reciprocal edges as to create an undirected graph
    ret = np.vstack((ret, ret[:,::-1]))

    return ret.T

@nb.njit
def delaunay_graph(data: nb.float64[:,:],
                   clusts: nb.types.List(nb.int64[:]),
                   batch_ids: nb.int64[:],
                   dist_mat: nb.float64[:,:] = None,
                   max_dist: nb.float64 = -1.) -> nb.int64[:,:]:
    """
    Function that returns an incidence matrix that connects nodes
    that share an edge in their corresponding Euclidean Delaunay graph.

    Args:
        data (np.ndarray)     : (N,4) [x, y, z, batchid]
        clusts ([np.ndarray]) : (C) List of arrays of voxel IDs in each cluster
        batch_ids (np.ndarray): (C) List of batch ids
        dist_mat (np.ndarray) : (C,C) Tensor of pair-wise cluster distances
        max_dist (double)     : Maximal edge length
    Returns:
        np.ndarray: (2,E) Tensor of edges
    """
    # For each batch, find the list of edges, append it
    ret = np.empty((0, 2), dtype=np.int64)
    for b in np.unique(batch_ids):
        clust_ids = np.where(batch_ids == b)[0]
        limits    = np.array([0]+[len(clusts[i]) for i in clust_ids]).cumsum()
        mask, labels = np.zeros(limits[-1], dtype=np.int64), np.zeros(limits[-1], dtype=np.int64)
        for i in range(len(clust_ids)):
            mask[limits[i]:limits[i+1]]   = clusts[clust_ids[i]]
            labels[limits[i]:limits[i+1]] = i*np.ones(len(clusts[clust_ids[i]]), dtype=np.int64)
        with nb.objmode(tri = 'int32[:,:]'): # Suboptimal. Ideally want to reimplement in Numba, but tall order...
            tri = Delaunay(data[mask,:3], qhull_options='QJ').simplices # Joggled input guarantees simplical faces
        adj_mat = np.zeros((len(clust_ids),len(clust_ids)), dtype=np.bool_)
        for s in tri:
            for i in s:
                for j in s:
                    if labels[j] > labels[i]:
                        adj_mat[labels[i],labels[j]] = True
        edges = np.where(adj_mat)
        edges = np.vstack((clust_ids[edges[0]],clust_ids[edges[1]])).T
        ret   = np.vstack((ret, edges))

    # If requested, remove the edges above a certain length threshold
    if max_dist > -1:
        assert dist_mat is not None
        dists = np.empty(len(ret), dtype=dist_mat.dtype)
        for k, e in enumerate(ret):
            dists[k] = dist_mat[e[0],e[1]]
        ret = ret[dists < max_dist]

    # Add the reciprocal edges as to create an undirected graph
    ret = np.vstack((ret, ret[:,::-1]))

    return ret.T


@nb.njit
def mst_graph(batch_ids: nb.int64[:],
              dist_mat: nb.float64[:,:] = None,
              max_dist: nb.float64 = -1.) -> nb.int64[:,:]:
    """
    Function that returns an incidence matrix that connects nodes
    that share an edge in their corresponding Euclidean Minimum Spanning Tree (MST).

    Args:
        batch_ids (np.ndarray): (C) List of batch ids
        dist_mat (np.ndarray) : (C,C) Tensor of pair-wise cluster distances
        max_dist (double)     : Maximal edge length
    Returns:
        np.ndarray: (2,E) Tensor of edges
    """
    # For each batch, find the list of edges, append it
    ret = np.empty((0, 2), dtype=np.int64)
    for b in np.unique(batch_ids):
        clust_ids = np.where(batch_ids == b)[0]
        if len(clust_ids) > 1:
            submat = np.triu(submatrix_nb(dist_mat, clust_ids, clust_ids))
            with nb.objmode(mst_mat = 'float32[:,:]'): # Suboptimal. Ideally want to reimplement in Numba, but tall order...
                mst_mat = minimum_spanning_tree(submat).toarray().astype(np.float32)
            edges = np.where(mst_mat > 0.)
            edges = np.vstack((clust_ids[edges[0]],clust_ids[edges[1]])).T
            ret   = np.vstack((ret, edges))

    # If requested, remove the edges above a certain length threshold
    if max_dist > -1:
        assert dist_mat is not None
        dists = np.empty(len(ret), dtype=dist_mat.dtype)
        for k, e in enumerate(ret):
            dists[k] = dist_mat[e[0],e[1]]
        ret = ret[dists < max_dist]

    # Add the reciprocal edges as to create an undirected graph
    ret = np.vstack((ret, ret[:,::-1]))

    return ret.T


@nb.njit
def knn_graph(batch_ids: nb.int64[:],
              k: nb.int64,
              dist_mat: nb.float64[:,:] = None) -> nb.int64[:,:]:
    """
    Function that returns an incidence matrix that connects nodes
    that are k nearest neighbors. Sorts the distance matrix.

    Args:
        batch_ids (np.ndarray): (C) List of batch ids
        k (int)               : Number of connected neighbors for each node
        dist_mat (np.ndarray) : (C,C) Tensor of pair-wise cluster distances
    Returns:
        np.ndarray: (2,E) Tensor of edges
    """
    # Use the available distance matrix to build a kNN graph
    ret = np.empty((0, 2), dtype=np.int64)
    for b in np.unique(batch_ids):
        clust_ids = np.where(batch_ids == b)[0]
        if len(clust_ids) > 1:
            subk = min(k+1, len(clust_ids))
            submat = submatrix_nb(dist_mat, clust_ids, clust_ids)
            for i in range(len(submat)):
                idxs = np.argsort(submat[i])[1:subk]
                edges = np.empty((subk-1,2), dtype=np.int64)
                for j, idx in enumerate(np.sort(idxs)):
                    edges[j] = [clust_ids[i], clust_ids[idx]]
                if len(edges):
                    ret = np.vstack((ret, edges))

    # Add the reciprocal edges as to create an undirected graph
    ret = np.vstack((ret, ret[:,::-1]))

    return ret.T


@nb.njit
def bipartite_graph(batch_ids: nb.int64[:],
                    primaries: nb.boolean[:],
                    dist_mat: nb.float64[:,:] = None,
                    max_dist: nb.float64 = -1,
                    directed: nb.boolean = True,
                    directed_to: str = 'secondary') -> nb.int64[:,:]:
    """
    Function that returns an incidence matrix of the bipartite graph
    between primary nodes and non-primary nodes.

    Args:
        batch_ids (np.ndarray): (C) List of batch ids
        primaries (np.ndarray): (C) Primary mask (True if primary)
        dist_mat (np.ndarray) : (C,C) Tensor of pair-wise cluster distances
        max_dist (double)     : Maximal edge length
    Returns:
        np.ndarray: (2,E) Tensor of edges
    """
    # Create the incidence matrix
    ret = np.empty((0,2), dtype=np.int64)
    for i in np.where(primaries)[0]:
        for j in np.where(~primaries)[0]:
            if batch_ids[i] ==  batch_ids[j]:
                ret = np.vstack((ret, np.array([[i,j]])))

    # If requested, remove the edges above a certain length threshold
    if max_dist > -1:
        assert dist_mat is not None
        dists = np.empty(len(ret), dtype=dist_mat.dtype)
        for k, e in enumerate(ret):
            dists[k] = dist_mat[e[0],e[1]]
        ret = ret[dists < max_dist]

    # Handle directedness, by default graph is directed towards secondaries
    if directed:
        if directed_to == 'primary':
            ret = ret[:,::-1]
        elif directed_to != 'secondary':
            raise ValueError('Graph orientation not recognized')
    else:
        ret = np.vstack((ret, ret[:,::-1]))

    return ret.T


@numba_wrapper(cast_args=['data'], list_args=['clusts'], keep_torch=True, ref_arg='data')
def get_cluster_edge_features(data, clusts, edge_index):
    """
    Function that returns a tensor of edge features for each of the
    edges connecting clusters in the graph.

    Args:
        data (np.ndarray)      : (N,8) [x, y, z, batchid, value, id, groupid, shape]
        clusts ([np.ndarray])  : (C) List of arrays of voxel IDs in each cluster
        edge_index (np.ndarray): (2,E) Incidence matrix
    Returns:
        np.ndarray: (E,19) Tensor of edge features (point1, point2, displacement, distance, orientation)
    """
    return _get_cluster_edge_features(data, clusts, edge_index)
    #return _get_cluster_edge_features_vec(data, clusts, edge_index)

@nb.njit(parallel=True)
def _get_cluster_edge_features(data: nb.float32[:,:],
                               clusts: nb.types.List(nb.int64[:]),
                               edge_index: nb.int64[:,:]) -> nb.float32[:,:]:

    feats = np.empty((len(edge_index), 19), dtype=data.dtype)
    for k in nb.prange(len(edge_index)):
        # Get the voxels in the clusters connected by the edge
        x1 = data[clusts[edge_index[k,0]],:3]
        x2 = data[clusts[edge_index[k,1]],:3]

        # Find the closest set point in each cluster
        d12 = cdist_nb(x1, x2)
        imin = np.argmin(d12)
        i1, i2 = imin//d12.shape[1], imin%d12.shape[1]
        v1 = x1[i1,:]
        v2 = x2[i2,:]

        # Displacement
        disp = v1 - v2

        # Distance
        lend = np.linalg.norm(disp)
        if lend > 0:
            disp = disp / lend

        # Outer product
        B = np.outer(disp, disp).flatten()

        feats[k] = np.concatenate((v1, v2, disp, np.array([lend]), B))

    return feats

@nb.njit
def _get_cluster_edge_features_vec(data: nb.float32[:,:],
                                   clusts: nb.types.List(nb.int64[:]),
                                   edge_index: nb.int64[:,:]) -> nb.float32[:,:]:

    # Get the closest points of approach IDs for each edge
    lend, idxs1, idxs2 = _get_edge_distances(data[:,:3], clusts, edge_index)

    # Get the points that correspond to the first voxels
    v1 = data[idxs1,:3]

    # Get the points that correspond to the second voxels
    v2 = data[idxs2,:3]

    # Get the displacement
    disp = v1 - v2

    # Reshape the distance vector to a column vector
    lend = lend.reshape(-1,1)

    # Normalize the displacement vector
    disp = disp/(lend + (lend == 0))

    # Compute the outer product of the displacement
    B = np.empty((len(disp), 9), dtype=data.dtype)
    for k in range(len(disp)):
        B[k] = np.outer(disp, disp).flatten()
    #B = np.dot(disp.reshape(len(disp),-1,1), disp.reshape(len(disp),1,-1)).reshape(len(disp),-1)

    return np.hstack((v1, v2, disp, lend, B))


@numba_wrapper(cast_args=['data'], keep_torch=True, ref_arg='data')
def get_voxel_edge_features(data, edge_index):
    """
    Function that returns a tensor of edge features for each of the
    edges connecting voxels in the graph.

    Args:
        data (np.ndarray)      : (N,8) [x, y, z, batchid, value, id, groupid, shape]
        edge_index (np.ndarray): (2,E) Incidence matrix
    Returns:
        np.ndarray: (E,19) Tensor of edge features (displacement, orientation)
    """
    return _get_voxel_edge_features(data, edge_index)

@nb.njit(parallel=True)
def _get_voxel_edge_features(data: nb.float32[:,:],
                         edge_index: nb.int64[:,:]) -> nb.float32[:,:]:
    feats = np.empty((len(edge_index), 19), dtype=data.dtype)
    for k in nb.prange(len(edge_index)):
        # Get the voxel coordinates
        xi = data[edge_index[k,0],:3]
        xj = data[edge_index[k,1],:3]

        # Displacement
        disp = xj - xi

        # Distance
        lend = np.linalg.norm(disp)
        if lend > 0:
            disp = disp / lend

        # Outer product
        B = np.outer(disp, disp).flatten()

        feats[k] = np.concatenate([xi, xj, disp, np.array([lend]), B])

    return feats


@numba_wrapper(cast_args=['voxels'], list_args='clusts')
def get_edge_distances(voxels, clusts, edge_index):
    """
    For each edge, finds the closest points of approach (CPAs) between the
    the two voxel clusters it connects, and the distance that separates them.

    Args:
        voxels (np.ndarray)    : (N,3) Tensor of voxel coordinates
        clusts ([np.ndarray])  : (C) List of arrays of voxel IDs in each cluster
        edge_index (np.ndarray): (E,2) Incidence matrix
    Returns:
        np.ndarray: (E) List of edge lengths
        np.ndarray: (E) List of voxel IDs corresponding to the first edge cluster CPA
        np.ndarray: (E) List of voxel IDs corresponding to the second edge cluster CPA
    """
    return _get_edge_distances(voxels, clusts, edge_index)

@nb.njit(parallel=True)
def _get_edge_distances(voxels: nb.float32[:,:],
                        clusts: nb.types.List(nb.int64[:]),
                        edge_index:  nb.int64[:,:]) -> (nb.float32[:], nb.int64[:], nb.int64[:]):

    resi, resj = np.empty(len(edge_index), dtype=np.int64), np.empty(len(edge_index), dtype=np.int64)
    lend = np.empty(len(edge_index), dtype=np.float32)
    for k in nb.prange(len(edge_index)):
        i, j = edge_index[k]
        if i == j:
            ii = jj = 0
            lend[k] = 0.
        else:
            dist_mat = cdist_nb(voxels[clusts[i]], voxels[clusts[j]])
            idx = np.argmin(dist_mat)
            ii, jj = idx//len(clusts[j]), idx%len(clusts[j])
            lend[k] = dist_mat[ii, jj]
        resi[k] = clusts[i][ii]
        resj[k] = clusts[j][jj]

    return lend, resi, resj


@numba_wrapper(cast_args=['voxels'], list_args=['clusts'])
def inter_cluster_distance(voxels, clsuts, batch_ids):
    """
    Finds the inter-cluster distance between every pair of clusters within
    each batch, returned as a block-diagonal matrix.

    Args:
        voxels (torch.tensor) : (N,3) Tensor of voxel coordinates
        clusts ([np.ndarray]) : (C) List of arrays of voxel IDs in each cluster
        batch_ids (np.ndarray): (C) List of cluster batch IDs
    Returns:
        torch.tensor: (C,C) Tensor of pair-wise cluster distances
    """
    return _inter_cluster_distance(voxels, clusts, batch_ids, mode)

@nb.njit(parallel=True)
def _inter_cluster_distance(voxels: nb.float64[:,:],
                            clusts: nb.types.List(nb.int64[:]),
                            batch_ids: nb.int64[:]) -> nb.float64[:,:]:

    dist_mat = np.zeros((len(batch_ids), len(batch_ids)), dtype=voxels.dtype)
    for i in nb.prange(len(batch_ids)):
        for j in range(len(batch_ids)):
            if batch_ids[i] == batch_ids[j]:
                if i < j:
                    dist_mat[i,j] = np.min(cdist_nb(voxels[clusts[i]], voxels[clusts[j]]))
                elif i > j:
                    dist_mat[i,j] = dist_mat[j,i]

    return dist_mat


@numba_wrapper(cast_args=['graph'])
def get_fragment_edges(graph, clust_ids):
    """
    Function that converts a set of edges between cluster ids
    to a set of edges between fragment ids (ordering in list)

    Args:
        graph (np.ndarray)    : (E,2) Tensor of [clust_id_1, clust_id_2]
        clust_ids (np.ndarray): (C) List of fragment cluster ids
        batch_ids (np.ndarray): (C) List of fragment batch ids
    Returns:
        np.ndarray: (E,2) Tensor of true edges [frag_id_1, frag_id2]
    """
    return _get_fragment_edges(graph, clust_ids)

@nb.njit
def _get_fragment_edges(graph: nb.int64[:,:],
                        clust_ids: nb.int64[:]) -> nb.int64[:,:]:
    # Loop over the graph edges, find the fragment ids, append
    true_edges = np.empty((0,2), dtype=np.int64)
    for e in graph:
        n1 = np.where(clust_ids == e[0])[0]
        n2 = np.where(clust_ids == e[1])[0]
        if len(n1) and len(n2):
            true_edges = np.vstack((true_edges, np.array([[n1[0], n2[0]]], dtype=np.int64)))

    return true_edges
