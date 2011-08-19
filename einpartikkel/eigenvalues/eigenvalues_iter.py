"""
eigenvalues
===========

Eigenvalue calculations module. Provides high-level interface to iterative
methods for finding some eigenvalues of a large Hamiltonian


Special config section
----------------------
A special config section is required by this module, to facilitate computations
and serialization of eigenpairs:

[Eigenvalues]
folder_name = "/path/to/eigenpairs  #where to store eigenpairs, unique to
									#potential
shift = -0.5                        #shift used in shift-invert procedure

"""

import os
import sys
from numpy import where, array, sort, unique
import tables
import pypar
import pyprop
from pyprop import GetFunctionLogger
from pyprop.serialization import RemoveExistingDataset
from ..utils import RegisterAll
from ..namegenerator import GetRadialPostfix

@RegisterAll
def FindEigenvaluesInverseIterationsPiram(conf):
	"""
	Calculates eigenvalues and eigenvectors for conf around a shift
	by using inverse iterations and pIRAM.

	Input
	-----
	conf: a pyprop config object

	Returns
	-------
	Piram solver object
	GMRES solver object
	Computed eigenvalues

	"""

	#Setup Problem
	prop = pyprop.Problem(conf)
	prop.SetupStep()

	#Setup shift invert solver in order to perform inverse iterations
	shiftInvertSolver = pyprop.GMRESShiftInvertSolver(prop)
	prop.Config.Arpack.inverse_iterations = True
	prop.Config.Arpack.matrix_vector_func = shiftInvertSolver.InverseIterations

	#Setup eiganvalue solver
	solver = pyprop.PiramSolver(prop)
	solver.Solve()

	#Get the converged eigenvalues
	ev = solver.Solver.GetEigenvalues().copy()
	estimates = solver.Solver.GetConvergenceEstimates().copy()
	idx = where(estimates < 0)[0]
	eigenvalues = ev[idx]

	#convert from shift inverted eigenvalues to "actual" eigenvalues
	shift = conf.Eigenvalues.shift
	eigenvalues = 1.0 / eigenvalues + shift

	return solver, shiftInvertSolver, eigenvalues


@RegisterAll
def SaveEigenpairsSolverShiftInvert(solver, shiftInvertSolver):
	"""
	Saves the output of FindEigenvaluesInverseIterationsPiram,
	including error estimates, to a hdf5 file.
	"""

	logger = GetFunctionLogger()

	conf = solver.BaseProblem.Config
	shift = conf.Eigenvalues.shift
	angularRank = 0

	#Get angular momentum and z-projection
	psi = solver.BaseProblem.psi
	angRange = \
		psi.GetRepresentation().GetRepresentation(angularRank).Range
	angSize = angRange.Count()
	l = sort(unique([angRange.GetLmIndex(i).l for i in range(angSize)]))
	m = sort(unique([angRange.GetLmIndex(i).m for i in range(angSize)]))

	#generate filename
	filename = NameGeneratorBoundstates(conf, l, m)

	#Get eigenvalue error estimates
	errorEstimatesPIRAM = solver.Solver.GetErrorEstimates()
	convergenceEstimatesEig = solver.Solver.GetConvergenceEstimates()
	errorEstimatesGMRES = shiftInvertSolver.Solver.GetErrorEstimateList()

	#Get eigenvalues
	prop = solver.BaseProblem
	E = 1.0 / array(solver.GetEigenvalues()) + shift

	#remove file if it exists
	try:
		if os.path.exists(filename):
			if pyprop.ProcId == 0:
				os.remove(filename)
	except:
		logger.error("Could not remove %s (%s)" % (filename, sys.exc_info()[1]))

	#Store eigenvalues and eigenvectors
	logger.info("Now storing eigenvectors...")
	for i in range(len(E)):
		solver.SetEigenvector(prop.psi, i)
		prop.SaveWavefunctionHDF(filename, GetEigenvectorDatasetPath(i))

	if pyprop.ProcId == 0:
		eigGroupName = GetEigenvectorGroupName()
		RemoveExistingDataset(filename, "/%s/Eigenvalues" % eigGroupName)
		RemoveExistingDataset(filename, "/%s/ErrorEstimateListGMRES" \
				% eigGroupName)
		RemoveExistingDataset(filename, "/%s/ErrorEstimateListPIRAM" \
				% eigGroupName)
		RemoveExistingDataset(filename, "/%s/ConvergenceEstimateEig" \
				% eigGroupName)
		h5file = tables.openFile(filename, "r+")
		try:
			#myGroup = h5file.createGroup("/", "Eig")
			myGroup = h5file.getNode(eigGroupName)
			h5file.createArray(myGroup, "Eigenvalues", E)
			h5file.createArray(myGroup, "ErrorEstimateListGMRES", errorEstimatesGMRES)
			h5file.createArray(myGroup, "ErrorEstimateListPIRAM", errorEstimatesPIRAM)
			h5file.createArray(myGroup, "ConvergenceEstimateEig", convergenceEstimatesEig)

			#Store config
			myGroup._v_attrs.configObject = prop.Config.cfgObj

			#PIRAM stats
			myGroup._v_attrs.opCount = solver.Solver.GetOperatorCount()
			myGroup._v_attrs.restartCount = solver.Solver.GetRestartCount()
			myGroup._v_attrs.orthCount = solver.Solver.GetOrthogonalizationCount()
		except:
			logger.warning("Warning: could not store eigenvalues and error estimates!")
		finally:
			h5file.close()

		pypar.barrier()


@RegisterAll
def LoadEigenpairs(conf, l, m):
	"""Load previously stored eigenpairs for given (l,m) pair

	Filename etc is autogenerated from config

	"""

	logger = GetFunctionLogger()

	#generate filename
	filename = NameGeneratorBoundstates(conf, l, m)

	#check if file exists, if not, return empty lists
	if not os.path.exists(filename):
		logger.warning("Eigenstates file does not exist, skipping: %s" %
				filename)
		return [], []

	#Load eigenvalues and eigenvectors
	logger.info("Now loading eigenvalues...")
	eigPath = GetEigenvectorGroupName()
	E = pyprop.serialization.GetArrayFromHDF5(filename, eigPath, "Eigenvalues")

	V = []
	logger.info("Now loading eigenvectors...")
	with tables.openFile(filename, "r") as h5file:
		for i in range(len(E)):
			myGroup = h5file.getNode(GetEigenvectorDatasetPath(i))
			V += [myGroup[0,:]]

	return E, V


@RegisterAll
def NameGeneratorBoundstates(conf, l, m):
	"""Returns a file name generated from a Pyprop config

	Parametres
	----------
	conf: config object.
	l:    (int) angular momentum
	m:    (int) m quantum number

	Returns
	-------
	fileName : string, excellent file name.

	"""
	#Folder
	folderName = conf.Eigenvalues.folder_name

	#Grid characteristics
	radialPostfix = "_".join(GetRadialPostfix(conf))

	l = str(l).replace(" ", "_")
	m = str(m).replace(" ", "_")

	filename = "%s/" % folderName
	filename += radialPostfix + "_l%s_m%s" % (l,m) + ".h5"

	return os.path.normpath(filename)


@RegisterAll
def GetEigenvectorDatasetName(idx):
	return "Eigenvector_%i" % idx

@RegisterAll
def GetEigenvectorGroupName():
	return "/Eig"

@RegisterAll
def GetEigenvectorDatasetPath(eigenvectorIndex):
	return "%s/%s" % (GetEigenvectorGroupName(), \
		GetEigenvectorDatasetName(eigenvectorIndex))

