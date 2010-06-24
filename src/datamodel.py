import logging
from random import randint
import numpy as np
from dbconnect import *
from singleton import *
from properties import Properties

p = Properties.getInstance()
db = DBConnect.getInstance()

class DataModel(Singleton):
    '''
    DataModel is a dictionary of perImageObjectCounts indexed by (TableNumber,ImageNumber)
    '''
    
    def __init__(self):
        self.data = {}           # {imKey:obCount, ... }
        self.groupMaps = {}      # { groupName:{imKey:groupKey, }, ... }
                                 # eg: groupMaps['Wells'][(0,4)]  ==>  (3,'A01')
        self.revGroupMaps = {}   # { groupName:{groupKey:imKey, }, ... }
                                 # eg: groupMaps['Wells'][(3,'A01')]  ==>  [(0,1),(0,2),(0,3),(0,4)]
        self.groupColNames = {}  # {groupName:[col_names], ...}
                                 # eg: {'Gene': ['gene'], ...}
        self.groupColTypes = {}  # {groupName:[col_types], ...}
        self.cumSums = []        # cumSum[i]: sum of objects in images 1..i (inclusive) 
        self.obCount = 0
        self.keylist = []
        self.filterkeys = {}     # sets of image keys keyed by filter name
        
    def __str__(self):
        return str(self.obCount)+" objects in "+ \
               str(len(self.data))+" images"
               
    def PopulateModel(self, delete_model=False):
        if delete_model:
            self.DeleteModel()
        elif not self.IsEmpty():
            # No op if already populated
            return
        
        if db is None:
            logging.error("Error: No database connection!")
            return
        
        if p.check_tables == 'yes':
            db.CheckTables()
        
        # Initialize per-image object counts to zero
        imKeys = db.GetAllImageKeys()
        for key in imKeys:
            key = tuple([int(k) for k in key])    # convert keys to to int tuples
            self.data[key] = 0
                    
        # Compute per-image object counts
        res = db.GetPerImageObjectCounts()
        for r in res:
            key = tuple([int(k) for k in r[:-1]])
            self.data[key] = r[-1]
            self.obCount += r[-1]
            
        self.keylist = list(self.data.keys())

        # Build a cumulative sum array to use for generating random objects quickly
        self.cumSums = np.zeros(len(self.data)+1, dtype='int')
        for i, imKey in enumerate(self.keylist):
            self.cumSums[i+1] = self.cumSums[i]+self.data[imKey]

        self.groupMaps, self.groupColNames = db.GetGroupMaps()
        self.revGroupMaps, _ = db.GetGroupMaps(reverse=True)
        for group in self.groupMaps:
            self.groupColTypes[group] = [type(col) for col in self.groupMaps[group].items()[0][1]]

    def DeleteModel(self):
        self.data = {}
        self.groupMaps = {}
        self.cumSums = []
        self.obCount = 0
        
    def _if_empty_populate(self):
        if self.IsEmpty:
            self.PopulateModel()
        
    def GetRandomFraction(self, num):
        self._if_empty_populate()
        logging.debug("RAND %s %s %s"%(num, self.obCount, int(-2**63 + 2**64 * float(num) / self.obCount)))
        return int(2**64 * float(num) / self.obCount - 2**63)

    def GetRandomObject(self):
        '''
        Returns a random object key
        We expect self.data.keys() to return the keys in the SAME ORDER
        every time since we build cumSums from that same ordering.  This
        need not necessarily be in sorted order.
        '''
        self._if_empty_populate()
        obIdx = randint(1, self.obCount)
        imIdx = np.searchsorted(self.cumSums, obIdx, 'left')
        # SUBTLETY: images which have zero objects will appear as repeated
        #    sums in the cumulative array, so we must pick the first index
        #    of any repeated sum, otherwise we are picking an image with no
        #    objects
        while self.cumSums[imIdx] == self.cumSums[imIdx-1]:
            imIdx -= 1
        imKey = self.data.keys()[imIdx-1]
        obIdx = obIdx-self.cumSums[imIdx-1]  # object number relative to this image
                    
        obKey = db.GetObjectIDAtIndex(imKey, obIdx)
        return obKey

    def GetRandomObjects(self, N, imKeys=None):
        '''
        Returns N random objects.
        If a list of imKeys is specified, GetRandomObjects will return 
        objects from only these images.
        '''
        self._if_empty_populate()
        if imKeys == None:
            return [self.GetRandomObject() for i in xrange(N)]
        elif imKeys == []:
            return []
        else:
            sums = np.cumsum([self.data[imKey] for imKey in imKeys])
            if sums[-1] < 1:
                return []
            obs = []
            for i in xrange(N):
                obIdx = randint(1, sums[-1])
                index = np.searchsorted(sums, obIdx, 'left')
                if index != 0:
                    while sums[index] == sums[index-1]:
                        index -= 1
                    obIdx = obIdx-sums[index-1]
                obKey = db.GetObjectIDAtIndex(imKeys[index], obIdx)
                obs.append(obKey)
            return obs
            
    def GetObjectsFromImage(self, imKey):
        self._if_empty_populate()
        obKeys=[]
        for i in xrange(1,self.GetObjectCountFromImage(imKey)+1):
            obKey = db.GetObjectIDAtIndex(imKey, i)
            obKeys.append(obKey)
        return obKeys
    
    def GetAllImageKeys(self, filter=None):
        ''' Returns all object keys. If a filter is passed in, only the image
        keys that fall within the filter will be returned.'''
        self._if_empty_populate()
        if filter is None:
            return list(self.data.keys())
        else:
            return list(db.GetFilteredImages(filter))

    def GetObjectCountFromImage(self, imKey):
        ''' Returns the number of objects in the specified image. '''
        self._if_empty_populate()
        return self.data[imKey]
    
    def GetImageKeysAndObjectCounts(self, filter=None):
        ''' Returns pairs of imageKeys and object counts. '''
        self._if_empty_populate()
        if filter is None:
            return self.data.items()
        else:
            return [(imKey, self.data[imKey]) for imKey in db.GetFilteredImages(filter)]
    
    def GetGroupColumnNames(self, group):
        ''' Returns the key column names associated with the specified group. '''
        self._if_empty_populate()
        return list(self.groupColNames[group])   # return a copy of this list so it can't be modified

    def GetGroupColumnTypes(self, group):
        ''' Returns the key column types associated with the specified group. '''
        self._if_empty_populate()
        return list(self.groupColTypes[group])   # return a copy of this list so it can't be modified

    def SumToGroup(self, imdata, group):
        '''
        Takes image data of the form:
           imdata = { imKey : np.array(values), ... }
        and sums the data into the specified group to return:
           groupdata = { groupKey : np.array(values), ... }
        '''
        self._if_empty_populate()
        groupData = {}
        nvals = len(imdata.values()[0])
        for imKey in imdata.keys():
            # initialize each entry to [0,0,...]
            groupData[self.groupMaps[group][imKey]] = np.zeros(nvals)
            
        for imKey, vals in imdata.items():
            # add values to running sum of this group
            groupData[self.groupMaps[group][imKey]] += vals
        
        return groupData
    
    def GetImagesInGroupWithWildcards(self, group, groupKey, filter=None):
        '''
        Returns all imKeys in a particular group. 
        '__ANY__' in the groupKey matches anything.
        '''
        self._if_empty_populate()
        if '__ANY__' in groupKey:
            # if there are wildcards in the groupKey then accumulate
            #   imkeys from all matching groupKeys
            def matches(key1, key2):
                return all([(a==b or b=='__ANY__') for a,b in zip(key1,key2)])
            imkeys = []
            for gkey, ikeys in self.revGroupMaps[group].items():
                if matches(gkey,groupKey):
                    imkeys += ikeys
            return imkeys
        else:
            # if there are no wildcards simply lookup the imkeys
            return self.GetImagesInGroup(group, groupKey, filter)
    
    def GetImagesInGroup(self, group, groupKey, filter=None):
        ''' Returns all imKeys in a particular group. '''
        self._if_empty_populate()
        try:
            imkeys = self.revGroupMaps[group][groupKey]
        except KeyError:
            return []
            
        # apply filter if supplied
        if filter is not None:
            if filter not in self.filterkeys.keys():
                self.filterkeys[filter] = set(db.execute(p._filters[filter]))
            imkeys = set(imkeys).intersection(self.filterkeys[filter])    
        
        return imkeys
    
    def GetGroupKeysInGroup(self, group):
        ''' Returns all groupKeys in specified group '''
        self._if_empty_populate()
        return list(set(self.groupMaps[group].values()))
        
    def IsEmpty(self):
        return self.data == {}


if __name__ == "__main__":
    p = Properties.getInstance()
    p.LoadFile('../properties/2009_02_19_MijungKwon_Centrosomes.properties')
    db = DBConnect.getInstance()
    db.connect()
    d = DataModel.getInstance()
    d.PopulateModel()