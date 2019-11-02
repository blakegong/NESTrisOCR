from PIL import Image, ImageDraw
from OCRAlgo.DigitOCR import scoreImage
from OCRAlgo.ScoreFixer import ScoreFixer
from OCRAlgo.PieceStatsTextOCR import generate_stats
import OCRAlgo.PieceStatsBoardOCR as PieceStatsBoardOCR
import OCRAlgo.BoardOCR as BoardOCR
import OCRAlgo.PreviewOCR3 as PreviewOCR
import OCRAlgo.CurrentPieceOCR as CurrentPieceOCR
from OCRAlgo.NewGameDetector import NewGameDetector

from calibrate import mainLoop as calibrateLoop
from config import config
from lib import * #bad!
from CachedSender import CachedSender
from multiprocessing import Pool
import multiprocessing
import threading
from Networking.NetworkClient import NetClient
from tkinter import messagebox, Tk
import time
import sys

#patterns for digits. 
#A = 0->9 + A->F, 
#D = 0->9
PATTERNS = {
    'score': 'ADDDDD' if config.hexSupport else 'DDDDDD',
    'lines': 'DDD',
    'level': 'DD',
    'stats': 'DDD',
    'cur_piece_das': 'DD'
}

STATS_METHOD = config.stats_method #can be TEXT or FIELD.
CAPTURE_FIELD = config.capture_field
CAPTURE_PREVIEW = config.capture_preview
CAPTURE_DAS_TRAINER = config.capture_das_trainer
STATS_ENABLE  = config.capture_stats
USE_STATS_FIELD = (STATS_ENABLE and STATS_METHOD == 'FIELD')
WINDOW_N_SLICE = config.tasksCaptureMethod == 'WINDOW_N_SLICE'
USE_COLOR_INTERPOLATION = config.colorMethod == 'INTERPOLATE'

#quick check for num_threads:
if WINDOW_N_SLICE and config.threads != 1:    
    messagebox.showerror("NESTrisOCR", "WINDOW_N_SLICE only supports one thread. Please change number of threads to 1") 
    sys.exit()
    
# The list of tasks to execute can be computed at bootstrap time
# to remove all conditional processing from the main running loop
def getWindowAreaAndPartialTasks():
    # gather list of all areas that need capturing
    # that will be used to determine the minimum window area to capture
    areas = {
        'score': mult_rect(config.CAPTURE_COORDS,config.scorePerc),
        'lines': mult_rect(config.CAPTURE_COORDS,config.linesPerc),
        'level': mult_rect(config.CAPTURE_COORDS,config.levelPerc)
    }

    if CAPTURE_FIELD:
        areas['field'] = mult_rect(config.CAPTURE_COORDS, config.fieldPerc)

        if USE_COLOR_INTERPOLATION:
            areas['black'] = mult_rect(config.CAPTURE_COORDS, config.blackPerc)
            areas['white'] = mult_rect(config.CAPTURE_COORDS, config.whitePerc)
        else:
            areas['color1'] = mult_rect(config.CAPTURE_COORDS, config.color1Perc)
            areas['color2'] = mult_rect(config.CAPTURE_COORDS, config.color2Perc)

    if USE_STATS_FIELD:
        areas['stats2'] = mult_rect(config.CAPTURE_COORDS, config.stats2Perc)
    elif STATS_ENABLE:
        areas['stats'] = mult_rect(config.CAPTURE_COORDS, config.statsPerc)

    if CAPTURE_PREVIEW:
        areas['preview'] = mult_rect(config.CAPTURE_COORDS, config.previewPerc)

    if CAPTURE_DAS_TRAINER:
        areas['cur_piece'] = mult_rect(config.CAPTURE_COORDS, config.currentPiecePerc)
        areas['cur_piece_das'] = mult_rect(config.CAPTURE_COORDS, config.currentPieceDasPerc)

    coords_list = areas.values()

    # compute the minimum window area to capture to cover all fields
    minWindowAreaTLRB = (
        min((coords[0] for coords in coords_list)),
        min((coords[1] for coords in coords_list)),
        max((coords[0] + coords[2] for coords in coords_list)),
        max((coords[1] + coords[3] for coords in coords_list)),
    )

    # convert minimum window coordinates to XYWH (needed by capture API)
    minWindowAreaXYWH = (
        minWindowAreaTLRB[0],
        minWindowAreaTLRB[1],
        minWindowAreaTLRB[2] - minWindowAreaTLRB[0],
        minWindowAreaTLRB[3] - minWindowAreaTLRB[1]
    )

    # Extract offset from minimal capture area
    offset = minWindowAreaXYWH[:2]

    partials = []

    def processCoordinates(coords, getCentralPixel=False):
        if getCentralPixel:
            coords = (
                round(lerp(coords[0], coords[0] + coords[2], 0.5)),
                round(lerp(coords[1], coords[1] + coords[3], 0.5)),
                1,
                1
            )

        if WINDOW_N_SLICE:
            return XYWHOffsetAndConvertToLTBR(offset, coords)
        else:
            return coords

    methodPrefix = 'extract' if WINDOW_N_SLICE else 'capture'

    # prepare list of tasks to run at each loop
    for key, coords in areas.items():
        if key in ('score', 'lines', 'level', 'cur_piece_das'):
            partials.append((
                eval(methodPrefix + 'AndOCR'),
                (
                    processCoordinates(coords),
                    PATTERNS[key],
                    key,
                    False,
                )
            ))

        elif key == 'preview':
            partials.append((
                eval(methodPrefix + 'AndOCRPreview'),
                (
                    processCoordinates(coords),
                )
            ))

        elif key == 'cur_piece':
            partials.append((
                eval(methodPrefix + 'AndOCRCurrentPiece'),
                (
                    processCoordinates(coords),
                )
            ))

        elif key == 'stats':
            stats_coords = generate_stats(config.CAPTURE_COORDS, config.statsPerc ,config.scorePerc[3])

            for pieceKey, pieceCoords in stats_coords.items():
                partials.append((
                    eval(methodPrefix + 'AndOCR'),
                    (
                        processCoordinates(pieceCoords),
                        PATTERNS[key],
                        pieceKey,
                        True,
                    )
                ))

        elif key == 'stats2':
            # stats2 will only be read as a task in the main loop IF multithreading is disabled
            if MULTI_THREAD == 1:
                partials.append((
                    eval(methodPrefix + 'AndOCRBoardPiece'),
                    (
                        processCoordinates(coords),
                    )
                ))

        elif key == 'field':
            # if interpolation is on, we must read the level before we can get the colors
            if USE_COLOR_INTERPOLATION:
                partials.append((
                    eval(methodPrefix + 'AndOCRBoardInterpolate'),
                    (
                        processCoordinates(coords),
                        processCoordinates(areas['black'], getCentralPixel=True),
                        processCoordinates(areas['white'], getCentralPixel=True),
                    )
                ))
            else:
                partials.append((
                    eval(methodPrefix + 'AndOCRBoard'),
                    (
                        processCoordinates(coords),
                        processCoordinates(areas['color1'], getCentralPixel=True),
                        processCoordinates(areas['color2'], getCentralPixel=True),
                    )
                ))


    return (minWindowAreaXYWH, partials)

#piece stats and method. Recommend using FIELD
STATS2_COORDS = mult_rect(config.CAPTURE_COORDS, config.stats2Perc)

MULTI_THREAD = config.threads #shouldn't need more than four if using FieldStats + score/lines/level

#limit how fast we scan.
RATE_FIELDSTATS = 1/60.0 if WINDOW_N_SLICE else 1/120.0
RATE_TEXTONLY = 0.064
RATE_FIELD = 1.0 / clamp(15,60,config.scanRate)

if USE_STATS_FIELD and MULTI_THREAD == 1:    
    RATE = RATE_FIELDSTATS
elif CAPTURE_FIELD:
    RATE = RATE_FIELD
else:
    RATE = RATE_TEXTONLY

#how are we calculating timestamp? Time.time, or from the file?
firstTime = time.time()
def getRealTimeStamp():
    return time.time() - firstTime
    
getTimeStamp = getRealTimeStamp
if config.captureMethod == 'FILE':
    MULTI_THREAD = 1
    if config.netProtocol == 'FILE':
        RATE = 0.000        
        getTimeStamp = WindowCapture.TimeStamp

SLEEP_TIME = 0.001
def captureAndOCR(hwnd, coords, digitPattern, taskName, red):
    img = WindowCapture.ImageCapture(coords, hwnd)
    return (taskName, scoreImage(img, digitPattern, False, red))

def captureAndOCRBoardPiece(hwnd, coords):
    img = WindowCapture.ImageCapture(coords, hwnd)
    rgbo = PieceStatsBoardOCR.parseImage(img)
    return ('piece_stats_board', rgbo)

def captureAndOCRBoard(hwnd, boardCoords, color1Coords, color2Coords):
    img = WindowCapture.ImageCapture(boardCoords, hwnd)
    col1 = WindowCapture.ImageCapture(color1Coords, hwnd)
    col2 = WindowCapture.ImageCapture(color2Coords, hwnd)
    field = BoardOCR.parseImageReadColors(img, col1, col2)
    return ('field', field)

def captureAndOCRBoardInterpolate(hwnd, level, boardCoords, blackCoords, whiteCoords):
    img = WindowCapture.ImageCapture(boardCoords, hwnd)
    black = WindowCapture.ImageCapture(blackCoords, hwnd)
    white = WindowCapture.ImageCapture(whiteCoords, hwnd)
    field = BoardOCR.parseImageInterpolateColors(img, black, white, level)
    return ('field', field)

def captureAndOCRPreview(hwnd, previewCoords):
    img = WindowCapture.ImageCapture(previewCoords, hwnd)
    result = PreviewOCR.parseImage(img)
    return ('preview', result)

def captureAndOCRCurrentPiece(hwnd, curPieceCoords):
    img = WindowCapture.ImageCapture(curPieceCoords, hwnd)
    result = CurrentPieceOCR.parseImage(img)
    return ('cur_piece', result)

def extractAndOCR(sourceImg, fieldCoords, digitPattern, taskName, red):
    img = sourceImg.crop(fieldCoords)
    return (taskName, scoreImage(img, digitPattern, False, red))

def extractAndOCRBoardPiece(sourceImg, boardPieceCoords):
    img = sourceImg.crop(boardPieceCoords)
    rgbo = PieceStatsBoardOCR.parseImage(img)
    return ('piece_stats_board', rgbo)

def extractAndOCRBoard(sourceImg, boardCoords, color1Coords, color2Coords):
    img = sourceImg.crop(boardCoords)
    col1 = sourceImg.crop(color1Coords)
    col2 = sourceImg.crop(color2Coords)
    field = BoardOCR.parseImageReadColors(img, col1, col2)
    return ('field', field)

def extractAndOCRBoardInterpolate(sourceImg, level, boardCoords, blackCoords, whiteCoords):
    img = sourceImg.crop(boardCoords)
    black = sourceImg.crop(blackCoords)
    white = sourceImg.crop(whiteCoords)
    field = BoardOCR.parseImageInterpolateColors(img, black, white, level)
    return ('field', field)

def extractAndOCRPreview(sourceImg, previewCoords):
    img = sourceImg.crop(previewCoords)
    result = PreviewOCR.parseImage(img)
    return ('preview', result)

def extractAndOCRCurrentPiece(sourceImg, curPieceCoords):
    img = sourceImg.crop(curPieceCoords)
    result = CurrentPieceOCR.parseImage(img)
    return ('cur_piece', result)

#run this as fast as possible    
def statsFieldMulti(ocr_stats, pool):
    while True:
        t = time.time()
        hwnd = getWindow()
        _, pieceType = pool.apply(captureAndOCRBoardPiece, (hwnd, STATS2_COORDS))
        ocr_stats.update(pieceType,t)
        if (time.time() - t > 1/60.0):
            print ("Warning, not scanning field fast enough", str(time.time() - t))
        
        # only sleep once.
        if time.time() < t + RATE_FIELDSTATS:
            time.sleep(SLEEP_TIME)


def main(onCap, checkNetworkClose):    
    if MULTI_THREAD >= 2:
        p = Pool(MULTI_THREAD)    
    else:
        p = None

    if USE_STATS_FIELD:
        accum = PieceStatsBoardOCR.OCRStatus()
        lastLines = None #use to reset accumulator
        if MULTI_THREAD >= 2: #run Field_OCR as fast as possible; unlock from mainthread.
            thread = threading.Thread(target=statsFieldMulti, args=(accum,p))
            thread.daemon = True
            thread.start()        
    
    scoreFixer = ScoreFixer(PATTERNS['score'])
    
    gameIDParser = NewGameDetector()
    finished = False
    while not finished:
        # outer loop waits for the window to exists
        frame_start = time.time()
        frame_end = frame_start + RATE
        hwnd = getWindow()

        if not hwnd:
            while time.time() < frame_end:
                time.sleep(SLEEP_TIME)
            continue

        windowMinCoords, partialTasks = getWindowAreaAndPartialTasks()

        if USE_COLOR_INTERPOLATION:
            fieldTask = [tasks for tasks in partialTasks if tasks[0]  in (captureAndOCRBoardInterpolate, extractAndOCRBoardInterpolate)].pop()
            partialTasks = [tasks for tasks in partialTasks if tasks[0] not in (captureAndOCRBoardInterpolate, extractAndOCRBoardInterpolate)]

        while checkWindow(hwnd):
            # inner loop gets fresh data for just the desired window
            frame_start  = time.time()
            frame_end = frame_start + RATE

            if WINDOW_N_SLICE:
                # capture min window area in one command first, will be sliced later
                source = WindowCapture.ImageCapture(windowMinCoords, hwnd)
            else:
                source = hwnd

            # inject source to complete partial tasks
            rawTasks = [(func, (source,) + args) for func, args in partialTasks]

            # run all tasks (in separate threads if MULTI_THREAD is enabled)
            result = runTasks(p, rawTasks)

            if config.hexSupport:
                #fix score's first digit. 8 to B and B to 8 depending on last state.
                result['score'] = scoreFixer.fix(result['score'])

            if USE_COLOR_INTERPOLATION:
                try:
                    level = int(result['level'])
                except:
                    level = 0

                key, value = runFunc(fieldTask[0], (source, level) + fieldTask[1])
                result[key] = value

            # update our accumulator
            if USE_STATS_FIELD:
                if lastLines is None and result['lines'] == '000':
                    accum.reset()
                
                if MULTI_THREAD == 1:
                    accum.update(result['piece_stats_board'], frame_start)
                    del result['piece_stats_board']

                result.update(accum.toDict())
                lastLines = result['lines']
            
                # warning for USE_STATS_FIELD if necessary
                if MULTI_THREAD == 1 and time.time() > frame_start + RATE_FIELD:
                    print ("Warning, dropped frame scanning preview in field")
            
            if config.capture_field and time.time() > frame_start + RATE_FIELD:
                print("Warning, dropped frame when capturing field")
            
            
            result['playername'] = config.player_name            
            result['gameid'], wasNewGameID = gameIDParser.getGameID(result['score'],result['lines'],result['level'])
            
            if config.hexSupport:
                if wasNewGameID:
                    scoreFixer.reset()
                #fix score's first digit. 8 to B and B to 8 depending on last state.
                result['score'] = scoreFixer.fix(result['score'])

            # print('frame', time.time() - frame_start)

            onCap(result, getTimeStamp())
            error = checkNetworkClose()   
            if error is not None:
                return error
            while time.time() < frame_end - SLEEP_TIME:
                time.sleep(SLEEP_TIME)
            
            if not WindowCapture.NextFrame(): #finished reading video
                finished = True
                break
    
if __name__ == '__main__':
    multiprocessing.freeze_support()
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == '--calibrate':
        calibrateLoop()
        sys.exit()


    print ("Creating net client...")
    client = NetClient.CreateClient(config.host,int(config.port))
    print ("Net client created.")
    cachedSender = CachedSender(client,config.printPacket,config.netProtocol)
    
    result = None
    try:
        print ("Starting main loop")
        result = main(cachedSender.sendResult, client.checkNetworkClose)
    except KeyboardInterrupt:
        pass
    print('main thread is here')
    print(result)
        
    if result is not None:
        #root = Tk()
        #root.withdraw()
        messagebox.showerror("NESTrisOCR", "You have been kicked. Reason: " + str(result)) 
        
    client.stop() 
    client.join()

