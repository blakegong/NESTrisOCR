import PIL
from PIL import Image, ImageEnhance, ImageFilter
import time
import numpy as np


import sys

data = {}
redData = {}
digits = ['0','1','2','3','4','5','6','7','8','9','null']

digitsMap = {
    'D': digits,
    'A': digits + ['A','B','C','D','E','F'],
    'B': ['0', '1', 'null'],
    'T': ['0', '1', '2', 'null'],
}

MONO = True
IMAGE_SIZE = 7
BLOCK_SIZE = IMAGE_SIZE+1
IMAGE_MULT = 3
GAP = (BLOCK_SIZE - IMAGE_SIZE) * IMAGE_MULT
SCALED_IMAGE_SIZE = IMAGE_SIZE * IMAGE_MULT

def finalImageSize(numDigits):
    return (((BLOCK_SIZE)*numDigits-1)*IMAGE_MULT, SCALED_IMAGE_SIZE)

def setupColour(prefix, outputDict, digitList):
    #setup white digits
    for digit in digitList:
        filename = prefix + str(digit) + '.png'
        if digit == 'null':
            filename = 'sprite_templates/null.png'
        img = Image.open('assets/' + filename)
        
        img = img.convert('L')
        if IMAGE_MULT != 1:
            img = img.resize((SCALED_IMAGE_SIZE,
                              SCALED_IMAGE_SIZE),PIL.Image.ANTIALIAS)
        
        img = img.getdata()
        img = np.asarray(img)
        img = np.reshape(img, (SCALED_IMAGE_SIZE, SCALED_IMAGE_SIZE))
        

        outputDict[digit] = img
        
def setupData():
    setupColour('sprite_templates/', data, digitsMap['A']) #setup white
    setupColour('sprite_templates/red', redData, digitsMap['D']) #setup red


def getDigit(img, pattern, startX, startY, red):
    template = redData if red else data
    validDigits = digitsMap[pattern]

    scores = {}
    #img in y, x format
    subImage = img[:,startX:startX + SCALED_IMAGE_SIZE]

    for digit in validDigits:
        diff = np.subtract(subImage, template[digit])
        diff = np.abs(diff)
        scores[digit] = np.sum(diff)
   
    lowest_score = float("inf")
    lowest_digit = None

    for digit, score in scores.items():
        if score < lowest_score:
            lowest_score = score
            lowest_digit = digit

    return (lowest_digit, lowest_score)

#convert to black/white, with custom threshold    
def contrastImg(img):  
    if MONO:
        img = img.convert('L')    
    #img = ImageEnhance.Brightness(img).enhance(2.0) # hack to parse red
    #img = ImageEnhance.Contrast(img).enhance(3.0)
    #img = ImageEnhance.Sharpness(img).enhance(1.5)
    return img
    
def convertImg(img, count, show):
    t = time.time()
    img = contrastImg(img)    
    img = img.resize(finalImageSize(count),PIL.Image.ANTIALIAS)
    
    if show:
        img.show()
        
    img = img.getdata()
    img = np.asarray(img)    
    img = np.reshape(img,(SCALED_IMAGE_SIZE,-1)) #img is in y,x format    
    
    return img    

#used for autocalibration.
def scoreImage0(img, digitPattern):
    score = []
    count = len(digitPattern)
    img = convertImg(img,count,False)
    
    for (i, pattern) in enumerate(digitPattern):
        result = getDigit(img, pattern, i*(BLOCK_SIZE*IMAGE_MULT),0,False)
        if result[0] != '0':
            return None
        else:            
            score.append(result[1])
    
    return sum(score)
        
def scoreImage(img, digitPattern, show=False, red=False):
    count = len(digitPattern)
    img = convertImg(img,count,show)
    label = ""
    for (i, pattern) in enumerate(digitPattern):
        result = getDigit(img, pattern, i*(BLOCK_SIZE*IMAGE_MULT),0, red)[0]
        if result == 'null':
            return None
        else:
            label += result
    return label

setupData()
    
def testFastOCR():
    setupData()
    import time
    
    t = time.time()
    
    img = Image.open("assets/test/score.png")
    for i in range(10000):
        scoreImage(img,'ADDDDD')
        
    result = time.time() - t
    print ("10000 iterations took:" + str(result) + " seconds")
    
    
if __name__ == '__main__':
    testFastOCR()