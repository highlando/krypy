# -*- coding: utf8 -*-
import numpy
import scipy.linalg
from . import utils, linsys


class ObliqueProjection(utils.Projection):
    def __init__(self, A, U, ip_B=None,
                 **kwargs):
        '''Oblique projection for (right) deflation.

        :param A: the linear operator (has to be compatible with
          :py:meth:`~krypy.utils.get_linearoperator`).
        :param U: basis of the deflation space with ``U.shape == (N, d)``.

        All parameters of :py:class:`~krypy.utils.Projection` are valid except
        ``X`` and ``Y``.
        '''
        # check and store input
        (N, d) = U.shape
        self.A = utils.get_linearoperator((N, N), A)
        U, _ = utils.qr(U, ip_B=ip_B)
        self.U = U

        # apply adjoint operator to U
        self.AH_U = A.adj*U

        # call Projection constructor
        super(ObliqueProjection, self).__init__(U, self.AH_U, **kwargs)

    def _correction(self, z):
        c = utils.inner(self.U, z, ip_B=self.ip_B)
        c = scipy.linalg.solve_triangular(self.WR.T.conj(), c, lower=True)
        if self.Q is not None and self.R is not None:
            c = scipy.linalg.solve_triangular(self.R, self.Q.T.conj().dot(c))
        return self.V.dot(c)

    def get_x0(self, pre):
        '''Get corrected initial guess for deflation.

        :param b: the right hand side. If a left preconditioner ``Ml`` is used,
          then it has to be applied to ``b`` before passing it to this
          function. This does not apply to the left preconditioner ``M`` due
          to the implicitly changed inner product.
        :param x0: (optional) the initial guess. Defaults to ``None`` which
          is treated as the zero initial guess.
        '''
        return pre.x0 + pre.Mr * self._correction(
            pre.Ml*(pre.b - pre.A*pre.x0))


class Arnoldifyer(object):
    def __init__(self, V, U, AU, H, B_, C, E, Pv_norm, U_v):
        r'''Obtain Arnoldi relations for approximate deflated Krylov subspaces.

        :param H: Hessenberg matrix :math:`\underline{H}_n`
          (``H.shape==(n+1,n)``) or :math:`H_n` (``H.shape==(n,n)``; invariant
          case) of already generated Krylov subspace.
        :param B_: :math:`\langle V_{n+1},AU\rangle` (``B_.shape==(n+1,d)``) or
          :math:`\langle V_n,AU\rangle` (``B_.shape==(n,d)``; invariant case).
        :param C: :math:`\langle U,AV_n\rangle` with ``C.shape==(d,n)``.
        :param E: :math:`\langle U,AU\rangle` with ``E.shape==(d,d)``.
        :param V: (optional) basis :math:`V_{n+1}` (``V.shape==(N,n+1)``) or
          :math:`V_n` (``V.shape==(N,n)``; invariant case).
        :param U: (optional) basis :math:`U` with ``U.shape==(N,d)``.
        '''
        # get dimensions
        n = self.n = H.shape[1]
        invariant = self.invariant = H.shape[0] == n
        d = self.d = U.shape[1]

        # store arguments
        self.V = V
        self.U = U
        self.AU = AU
        self.H = H
        self.B_ = B_
        self.C = C
        self.E = E
        self.Pv_norm = Pv_norm
        self.U_v = U_v

        # store a few matrices for later use
        EinvC = numpy.linalg.solve(E, C) if d > 0 else numpy.zeros((0, n))
        self.L = numpy.bmat([[H, numpy.zeros((n+1, d))],
                             [EinvC, numpy.eye(d)]])
        self.J = numpy.bmat([[numpy.eye(n, n+1), B_[:n, :]],
                             [numpy.zeros((d, n+1)), E]])
        self.M = numpy.bmat([[H[:n, :n]
                              + B_[:n, :].dot(EinvC),
                              B_[:n, :]],
                             [C, E]])
        self.A_norm = numpy.linalg.norm(self.M, 2)

        if d > 0:
            # rank-revealing QR decomp of projected AU
            Q, R, P = scipy.linalg.qr(AU - U.dot(E) - V.dot(B_),
                                      mode='economic', pivoting=True)
            P_inv = numpy.argsort(P)

            # rank of R
            l = (numpy.abs(numpy.diag(R)) > 1e-14*self.A_norm).sum()
            Q1 = Q[:, :l]
            R12 = R[:l, :]
            # residual helper matrix
            self.N = numpy.bmat([[[[1]], B_[[-1], :]],
                                 [numpy.zeros((l, 1)), R12[:, P_inv]]
                                 ]).dot(numpy.bmat([[numpy.zeros((d+1, n)),
                                                    numpy.eye(d+1)]]))
        else:
            Q1 = numpy.zeros((self.U.shape[0], 0))
            self.N = numpy.bmat([[numpy.zeros((1, n)),
                                  numpy.eye(1)]])

        # residual basis
        self.Z = numpy.c_[V[:, [-1]], Q1]

    def get(self, Wt, full=False):
        n = self.n
        invariant = self.invariant
        d = self.d
        k = Wt.shape[1]

        PtE = Wt.T.conj().dot(self.M.dot(Wt))
        Pt = numpy.eye(n+d+1) - self.L.dot(Wt.dot(
            numpy.linalg.solve(PtE, Wt.T.conj().dot(self.J))))
        if d > 0:
            qt = Pt.dot(numpy.r_[[[self.Pv_norm]], numpy.zeros((n, 1)),
                                 numpy.linalg.solve(self.E, self.U_v)])
        else:
            qt = Pt.dot(numpy.r_[[[self.Pv_norm]], numpy.zeros((n, 1))])
        q = self.J.dot(qt)

        # rotate closest vector in [V_n,U] to first column
        Q = utils.House(q)

        # Arnoldify
        LQ = Q.apply(self.L.T.conj()).T.conj()
        H, T = scipy.linalg.hessenberg(Q.apply(self.J).dot(Pt.dot(LQ)),
                                       calc_q=True)
        QT = Q.apply(T)

        # construct residual
        R = self.N.dot(Pt.dot(self.L.dot(QT)))

        if full:
            Vh = numpy.c_[self.V[:, :n], self.U].dot(QT)
            F = - self.Z.dot(R.dot(Vh.T.conj()))
            F = F + F.T.conj()
            return H, R, Vh, F
        return H, R