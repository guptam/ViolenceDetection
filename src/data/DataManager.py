from abc import ABCMeta, abstractmethod
import random
import numpy as np
import time
import threading
import settings.DataSettings as dataSettings
from src.data.VideoData import VideoData
import six
if six.PY2:
	from Queue import *
else:
	from queue import *

class BatchData:
	def __init__(self):
		self.numberOfUnrolls = 0
		self.batchOfImages = None
		self.batchOfLabels = None

class DataManagerBase:
	__metaclass__ = ABCMeta
	def __init__(self, PATH_TO_DATA_SET_CATELOG_):
		self._listOfData = []
		self._initVideoData(PATH_TO_DATA_SET_CATELOG_)
		self.TOTAL_DATA = len(self._listOfData)

		self._lockForStopThread = threading.Lock()
		self._shouldStop = False
		self._queueForWaitingVideos = Queue(maxsize=dataSettings.DATA_QUEUE_MAX_SIZE*2)
		self._queueForLoadedVideos = Queue(maxsize=dataSettings.DATA_QUEUE_MAX_SIZE)
		self._loadVideoThread = threading.Thread(target=self._loadVideo, args=())
		self._loadVideoThread.start()

	def Stop(self):
		with self._lockForStopThread:
			self._shouldStop = True

	def __del__(self):
		self.Stop()
		self._loadVideoThread.join()
		print("\t DataManagerBase.thread.join() successfully.")

	def _initVideoData(self, PATH_TO_DATA_SET_CATELOG_):
		'''
		    The data are expected in the following format:
		'''
		with open(PATH_TO_DATA_SET_CATELOG_, 'r') as fileContext:
			for eachLine in fileContext:
				try:
					pathToVideo, fightStartFrame, fightEndFrame = eachLine.split('\t')
					currentVideo = VideoData(pathToVideo, fightStartFrame, fightEndFrame)

					self._listOfData.append( currentVideo )

				except Exception as error:
					print(error)
		if len(self._listOfData) == 0:
			raise ValueError("No Valid Data found in: " + PATH_TO_DATA_SET_CATELOG_)

	def _loadVideo(self):
		shouldStop = False
		with self._lockForStopThread:
			shouldStop = self._shouldStop

		while not shouldStop:
			if self._queueForLoadedVideos.qsize() <= dataSettings.DATA_QUEUE_MAX_SIZE:
				try:
					videoReader = self._queueForWaitingVideos.get(block=False)
					videoReader.LoadVideoImages()
					self._queueForLoadedVideos.put(videoReader, block=True)

				except Empty:
					time.sleep(0.0005)

				# Update shouldStop flag
				with self._lockForStopThread:
					shouldStop = self._shouldStop
			else:
				time.sleep(0.001)


	def _getDataFromSingleVideo(self, video_, startFrameIndex_, NUMBER_OF_FRAMES_TO_CONCAT_):
		endFrameIndex = startFrameIndex_ + NUMBER_OF_FRAMES_TO_CONCAT_
		if endFrameIndex <= video_.totalFrames:
			arrayOfImages = video_.images[startFrameIndex_ : endFrameIndex]
			arrayOfLabels = video_.labels[startFrameIndex_ : endFrameIndex]
			return arrayOfImages, arrayOfLabels

		else:
			'''
			    For the case that UNROLLED_SIZE > video.TOTAL_FRAMES,
			    use the last frame always.
			'''
			print("video.totalFrames=", video_.totalFrames, "; while UNROLL = ", NUMBER_OF_FRAMES_TO_CONCAT_)
			arrayOfImages = np.zeros( [NUMBER_OF_FRAMES_TO_CONCAT_,
						   dataSettings.IMAGE_SIZE, dataSettings.IMAGE_SIZE, 3] )
			arrayOfLabels = np.zeros( [NUMBER_OF_FRAMES_TO_CONCAT_, 2] )
			
			arrayOfImages[ : video_.totalFrames] = video_.images[startFrameIndex_:]
			arrayOfLabels[ : video_.totalFrames] = video_.labels[startFrameIndex_:]

			numberOfArtificialFrames = endFrameIndex - video_.totalFrames
			arrayOfLastFrameImages = np.tile( video_.images[-1], [numberOfArtificialFrames, 1, 1, 1] )
			arrayOfLastFrameLabels = np.tile( video_.labels[-1], [numberOfArtificialFrames, 1] )

			arrayOfImages[video_.totalFrames : ] = arrayOfLastFrameImages
			arrayOfLabels[video_.totalFrames : ] = arrayOfLastFrameLabels

			return arrayOfImages, arrayOfLabels

	def _pushVideoDataToWaitingQueue(self, numberOfData_):
		'''
		    This function push 'numberOfData_' from the head of 'self._listOfData'
		    to the queue that wait for loading video images.
		    Note: If the '_queueForWaitingVideos' is full, ignore push.
		'''
		for i in range(numberOfData_):
			try:
				videoReader = self._listOfData.pop(0)
				self._queueForWaitingVideos.put(videoReader, block=False)

			except Full:
				self._listOfData.append(videoReader)

			except IndexError:
				'''
				    For the case that DATA_QUEUE_MAX_SIZE > TOTAL_DATA,
				    the IndexError may be raised from '_listOfData.pop()'.
				'''
				pass

	def _popVideoDataFromLoadedQueue(self, numberOfData_):
		'''
		    This function pop 'numberOfData_' from the queue that contained
		    loaded VideoData, and return them as list.
		    Note: This function may Blcok the Caller.  However, if you have
			  call the '_pushVideoDataToWaitingQueue()' which will
			  remain the queue has certain element, this function may
			  not Block the Caller.
		'''
		listOfLoadedVideo = []
		for i in range(numberOfData_):
			videoData = self._queueForLoadedVideos.get(block=True)
			listOfLoadedVideo.append(videoData)

		return listOfLoadedVideo

	def _appendVideoDataBackToDataList(self, listOfVideoData_):
		'''
		    After you get the video from '_popVideoDataFromLoadedQueue()'
		    and perform some operation on the videos, you should stuff that
		    VideoReader back to the 'self._listOfData'.  Otherwise the
		    VideoReader will getting fewer and fewer.
		'''
		self._listOfData += listOfVideoData_

	def GetQueueInfo(self):
		info = "listOfData.len() = " + str( len(self._listOfData) ) + ";\t"
		info += "queueForWaiting.len() = " + str( self._queueForWaitingVideos.qsize() ) + ";\t"
		info += "queueForLoaded.len() = " + str(self._queueForLoadedVideos.qsize() ) + ";\t"
		return info

	@abstractmethod
	def GetBatchOfData(self):
		pass


class TrainDataManager(DataManagerBase):
	def __init__(self):
		super().__init__(dataSettings.PATH_TO_TRAIN_SET_LIST)
		self._pushVideoDataToWaitingQueue(dataSettings.DATA_QUEUE_MAX_SIZE)
		self._isNewEpoch = True
		self._dataCursor = 0

		self.epoch = 0
		self.step = 0

	def GetBatchOfData(self, batchData_):
		'''
		    The user should pass BatchData as argument to this function,
		    since this would be faster then this function return two numpy.array.
		'''
		self._isNewEpoch = False

		arrayOfBatchImages = np.zeros( [dataSettings.BATCH_SIZE, dataSettings.UNROLLED_SIZE,
					        dataSettings.IMAGE_SIZE, dataSettings.IMAGE_SIZE, 3] )
		arrayOfBatchLabels = np.zeros( [dataSettings.BATCH_SIZE, dataSettings.UNROLLED_SIZE, 2] )

		listOfLoadedVideos = self._popVideoDataFromLoadedQueue(dataSettings.BATCH_SIZE)

		startLoopTime = time.time()
		outputIndex = 0
		while outputIndex < dataSettings.BATCH_SIZE:
			currentVideo = listOfLoadedVideos[outputIndex]
			frameStartIndex = random.randint(0, max(0, currentVideo.totalFrames - dataSettings.UNROLLED_SIZE) )

			startGetImagesTime = time.time()
			arrayOfImages, arrayOfLabels = self._getDataFromSingleVideo(currentVideo,
										    frameStartIndex, dataSettings.UNROLLED_SIZE)
			endGetImagesTime = time.time()
			print("\t\t _getDataFromSingleVideo time: ", endGetImagesTime - startGetImagesTime)

			# Release the video frames
			currentVideo.ReleaseImages()

			startAssignTime = time.time()
			arrayOfBatchImages[outputIndex] = arrayOfImages
			arrayOfBatchLabels[outputIndex] = arrayOfLabels
			endAssignTime = time.time()
			print("\t\t arrayOfBatchLabels[outputIndex] = currentImages time: ", endAssignTime - startAssignTime)

			outputIndex += 1
			self._dataCursor += 1
			if self._dataCursor >= self.TOTAL_DATA:
				random.shuffle(self._listOfData)
				self._dataCursor = 0
				self.epoch += 1
				self.isNewEpoch = True

		self.step += 1

		endLoopTime = time.time()
		print("\t while loop time: ", endLoopTime - startLoopTime)

		self._pushVideoDataToWaitingQueue(dataSettings.BATCH_SIZE)
		self._appendVideoDataBackToDataList(listOfLoadedVideos)

		batchData_.numberOfUnrolls = dataSettings.UNROLLED_SIZE
		batchData_.batchOfImages = arrayOfBatchImages.reshape( [-1, dataSettings.IMAGE_SIZE, dataSettings.IMAGE_SIZE, 3] )
		batchData_.batchOfLabels = arrayOfBatchLabels.reshape( [-1, 2] )


class EvaluationDataManager(DataManagerBase):
	'''
	    This DataManager is design for Validation & Test.
	    Different from TrainDataManager, EvaluationDataManager
	    will try to pach the Same Video into a batch.  And if
	    there're more space, this manager will not keep packing
	    images from other video.

	    Usage:
		def CalculateValidation():
			valDataSet = EvaluationDataManager("./val.txt")

			valLoss = 0
			while not valDataSet.isAllDataTraversed:
				valLoss += net.CalculateLoss(valDataSet.GetBatchOfData())
				if valDataSet.isNewVideo:
					net.ResetCellState()
	'''
	def __init__(self, PATH_TO_DATA_SET_CATELOG_):
		super().__init__(PATH_TO_DATA_SET_CATELOG_)
		self._pushVideoDataToWaitingQueue(dataSettings.DATA_QUEUE_MAX_SIZE)
		self.isAllDataTraversed = False
		self.isNewVideo = True
		self._dataCursor = 0
		self._currentVideo = None
		self._frameCursor = 0

	def GetBatchOfData(self, batchData_):
		'''
		    The user should pass BatchData as argument to this function,
		    since this would be faster then this function return two numpy.array.
		'''
		self.isAllDataTraversed = False
		self.isNewVideo = False
		if self._currentVideo == None:
			self._currentVideo = self._popVideoDataFromLoadedQueue(1)[0]

		unrolledSize = min(dataSettings.BATCH_SIZE * dataSettings.UNROLLED_SIZE,
				   self._currentVideo.totalFrames - self._frameCursor)

		batchData_.numberOfUnrolls = unrolledSize
		batchData_.batchOfImages, batchData_.batchOfLabels = self._getDataFromSingleVideo(self._currentVideo,
												  self._frameCursor, unrolledSize)
		self._frameCursor += unrolledSize

		if self._frameCursor >= self._currentVideo.totalFrames:
			self._frameCursor = 0
			self._dataCursor += 1
			self.isNewVideo = True

			self._pushVideoDataToWaitingQueue(1)
			self._appendVideoDataBackToDataList( [self._currentVideo] )

			self._currentVideo.ReleaseImages()
			self._currentVideo = None
		
			if self._dataCursor >= self.TOTAL_DATA:
				self._dataCursor = 0
				self.isAllDataTraversed = True

