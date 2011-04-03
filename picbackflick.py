#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PicBackFlic is a Flickr backup utility.
"""

# picbackflic.py - Reed Wade <reedwade@gmail.com>, 2011-04-03
#
# This file is part of PicBackFlic.
#
# PicBackFlic is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# PicBackFlic is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with PicBackFlic.  If not, see <http://www.gnu.org/licenses/>.


## TODO: make this work for video
## TODO: implement public-only feature
## TODO: implement single photo fetch by ID
## TODO: better exception handling 


## PicBackFlic depends on the Python Flickr API (previously known as "Beej's Python Flickr API")
## as seen at: http://stuvel.eu/flickrapi
## If you have a recent Debian / Ubuntu setup you should be able to install it via--
##   sudo apt-get install python-flickrapi
## Otherwise, see the installation instructions at the web site above.

import flickrapi

import sys
import os
import time
import urllib2
import json
from optparse import OptionParser
import ConfigParser


# Sometimes Flickr's API service doesn't want to talk to the world but that clears up pretty quickly.
# So, try again instead of giving up. Add this decorator to get yourself 3 free failures.
netfail_retries = 3 
netfail_wait = 30
def network_retry(fn):
    def wrapped(*args, **kwargs):
        retries = netfail_retries
        while retries > 0:
            try:
                return fn(*args, **kwargs)
            except urllib2.HTTPError as e:
                retries -= 1
                print "error", e, "will try again in", netfail_wait, "seconds"
                time.sleep(netfail_wait)
            except urllib2.URLError as e:
                retries -= 1
                print "error", e, "will try again in", netfail_wait, "seconds"
                time.sleep(netfail_wait)
            except IOError as e:
                retries -= 1
                print "error", e, "will try again in", netfail_wait, "seconds"
                time.sleep(netfail_wait)
        # last chance; if you fail again we let a higher layer deal with it
        return fn(*args, **kwargs)
    return wrapped


class Photo:
    """
    This class represents a single photo or video.
        p = Photo(picbackflick_instance, flickr_photo_id)
        print p.vals['description']
        print p.get_image_url('s')
    """
    
    def __init__(self, pbf, id):
        self.pbf = pbf
        self.id = id
        self.get_info_from_id()
            
    @network_retry
    def get_info_from_id(self):
        """
        Load photo details from Flickr into self.val dictionary.
        """
        
        photo_info = self.pbf.flickr.photos_getInfo(photo_id=self.id)
        if photo_info.attrib['stat'] != 'ok':
            raise RuntimeError("bad stat " + photo_info.attrib['stat'])
        photo_info = photo_info.find('photo')
        # photo_info.attrib will contain: originalsecret, secret, farm, server, media, dateuploaded, originalformat (and more)
        self.vals = photo_info.attrib
        
        self.vals['title'] = photo_info.find('title').text
        if self.vals['title'] == None:
            self.vals['title'] = ''
        
        self.vals['description'] = photo_info.find('description').text
        if self.vals['description'] == None:
            self.vals['description'] = ''
            
        dates = photo_info.find('dates')
        self.vals['datetaken'] = dates.attrib['taken']
        ## TODO: extract much more information from photo_info which we want to archive
        
        
    def get_image_url(self, size='o', prefix='http://'):
        """
        Returns the url for this image at the given size.
        
        We use this function to generate local file names. So, the prefix is settable with the default being correct for the Flickr copy of the image.
        """

        valid_sizes = ['o','s','t','m','','z','b']
        if size not in valid_sizes:
            raise RuntimeError("bad option for size: %s must be one of %s" % (size, str(valid_sizes)) )
            
        if size == 'o':
            return "%sfarm%s.static.flickr.com/%s/%s_%s_o.%s" \
                % (prefix, self.vals['farm'], self.vals['server'], self.id, self.vals['originalsecret'], self.vals['originalformat'])
        else:
            return "%sfarm%s.static.flickr.com/%s/%s_%s%s%s.jpg" \
                % (prefix, self.vals['farm'], self.vals['server'], self.id, self.vals['secret'], '' if size=='' else '_', size)
    
    @network_retry
    def save(self):
        """
        Store the original image and meta data locally.
        """
        
        ## store images
        for size in self.pbf.options.store_image_sizes:
            image_filename = self.get_image_url(size=size, prefix='img/')
            self.vals['image_'+size] = image_filename

            f = os.path.join(self.pbf.options.photos_path,image_filename)
            
            if os.path.exists(f):
                self.pbf.info("skipping "+f)
                continue
            if not os.path.exists(os.path.dirname(f)):
                os.makedirs(os.path.dirname(f)) ## TODO: deal with failures in dir creation
                
            self.pbf.info("writing "+f)
            img = urllib2.urlopen(self.get_image_url(size=size))
            out = open(f,'wb')
            out.write(img.read()) ## TODO: need to look at chunking this up instead of single buffer
            out.close()
            img.close()

        ## meta data
        f = os.path.join(self.pbf.options.photos_path,'info',self.id[-2:],self.id+".js")
        self.pbf.info("writing "+f)
        if not os.path.exists(os.path.dirname(f)):
            os.makedirs(os.path.dirname(f)) ## TODO: deal with failures in dir creation
        out = open(f,'wb')
        # we use dateuploaded as the key along with ID because we want to sort on this later
        # it turns out Flickr photo IDs aren't strictly sequential
        out.write("picbackflick_images['"+self.vals['dateuploaded']+"_"+self.id+"'] = "+json.dumps(self.vals)+"\n")
        out.close()

class PicBackFlick:

    def __init__(self):
        self.start_time = int(time.time())
        
        
    def info(self, message):
        if self.options.verbose:
            print message
    
    
    def update_web_pages(self):
        """
        We have new photo data so update the local web pages as needed.
        
        Right now this part of the app is a bit rough and dirty.
        We cat all the little js files (1 per photo) into a single js file which is loaded by the clever static web page.
        
        This won't scale and needs work.
        """
        
        filename = os.path.join(self.options.photos_path,"photo_db.js")
        self.info("writing "+filename)
        out = open(filename, "wb")
        out.write("var picbackflick_images = {}\n")
        out.write("var flickr_username = '"+self.options.flickr_username+"'\n")

        # walk the info directory and collect all the file contents
        
        for root, dirs, files in os.walk(os.path.join(self.options.photos_path,"info")):
            for f in files:
                f = open(os.path.join(root,f))
                out.write(f.read())
                f.close()
            
        out.close()


    def get_last_updated_timestamp(self):
        if os.path.exists(self.options.last_updated_filename):
            f = open(self.options.last_updated_filename)
            t = f.readline()
            f.close()
            return t
        else:
            # default value of minimum time stamp (it didn't like zero, 1 works tho)
            return '1'


    def set_last_updated_timestamp(self):
        if not os.path.exists(os.path.dirname(self.options.last_updated_filename)):
            os.makedirs(os.path.dirname(self.options.last_updated_filename)) ## TODO: deal with failures in dir creation
        out = open(self.options.last_updated_filename, "wb")
        out.write("%d\n" % self.start_time)
        out.close()
        return 1


    def load_config_file(self):
    
        if not os.path.exists(self.options.picbackflick_conf):
            f = open(self.options.picbackflick_conf, "wb")
            f.write("""
[picbackflick]

#
# You must set the api_key and api_secret. Go to http://www.flickr.com/services/api/keys/apply/
# and fill in the form to get yours.
#
## api_key = XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
## api_secret = XXXXXXXXXXXXXXXX

#
# flickr_username is used to construct the photo page url
#
## flickr_username = reedwade

#
# photos_path is where your photos and information land
#
photos_path = ~/flickr_backups

""")
            f.close()
            print self.options.picbackflick_conf, "doesn't exist, a new one has been created for you which you must now edit"
            sys.exit(1)
        
        self.info("reading "+self.options.picbackflick_conf)
        config = ConfigParser.RawConfigParser()
        config.read(self.options.picbackflick_conf)

        try:
            self.api_key = config.get('picbackflick', 'api_key')
            self.api_secret = config.get('picbackflick', 'api_secret')
            self.options.photos_path = config.get('picbackflick', 'photos_path')
            self.options.flickr_username = config.get('picbackflick', 'flickr_username')
        except ConfigParser.NoOptionError:
            print "These options must be set in", self.options.picbackflick_conf, ": api_key, api_secret, flickr_username, photos_path"
            sys.exit(1)
        
        self.options.photos_path = os.path.expanduser(self.options.photos_path)
        
        self.options.last_updated_filename = os.path.join(self.options.photos_path,"last_updated")
        
        self.options.store_image_sizes = ['s','','o']
        

        
    def handle_command_line_options(self):
        
        parser = OptionParser()
        parser.add_option("-d", "--days-ago",       dest="days_ago",           metavar="DAYS", type="int",
                          help="how many days ago to look backward, default is to the start of your photostream unless timestamp file is found")
        parser.add_option("-q", "--quiet",          dest="verbose",            action="store_false", default=True,
                          )
        parser.add_option("-f", "--config-file",    dest="picbackflick_conf",   metavar="FILE", default="~/.picbackflick.conf",
                          )
        parser.add_option("-i", "--ids-only",       dest="ids_only",           action="store_true", default=False,
                          help="get a list of updated photo IDs only")
        parser.add_option("-w", "--reset-web-info", dest="reset_web_info",     action="store_true", default=False,
                          help="rebuild the local photo javascript db file")
        
        ## TODO: implement single photo fetch by ID
        ##parser.add_option("-s", "--single", dest="single_photo", metavar="PHOTO-ID",
        ##                  help="update a single photo entry")

        ## TODO: implement public only feature
        ##parser.add_option("--public-photos-only", dest="public_photos_only", action="store_true", default=False,
        ##                  help="only fetch images and data for publicly viewable items")
                          

        (self.options, args) = parser.parse_args()
        
        self.options.picbackflick_conf = os.path.expanduser(self.options.picbackflick_conf)
        
        self.load_config_file()

        seconds_per_day = 60*60*24

        if self.options.days_ago:
            self.min_date = self.start_time - (self.options.days_ago * seconds_per_day)
        else:
            self.min_date = int(self.get_last_updated_timestamp())
        
        self.info("photos path:           "+self.options.photos_path)
        self.info("photos starting from:  "+str(self.min_date))


    @network_retry
    def authenticate(self):
        """
        This is the normal authentication scheme handled by FlickrAPI.
        The token is cached in ~/.flickr/$api_key/auth.token for reuse.
        """
        self.flickr = flickrapi.FlickrAPI(self.api_key, self.api_secret, format='etree')

        (token, frob) = self.flickr.get_token_part_one(perms='write')
        if not token: raw_input("Press ENTER after you authorized this program")
        self.flickr.get_token_part_two((token, frob))


    def get_recent_photos(self):
        """
        This is a convenience function that handles the typical set of work.
         - parse command line, authenticate, process photos. 
        """
        self.handle_command_line_options()
        
        # user just wants a rebuild of the javascript DB file
        if self.options.reset_web_info:
            self.update_web_pages()
            sys.exit(0)
            
        self.authenticate()
        return self._get_recent_photos()
        
        
    @network_retry
    def photos_recentlyUpdated(self, per_page, page, min_date):
        return self.flickr.photos_recentlyUpdated(per_page=per_page, page=page, min_date=min_date)

    def _get_recent_photos(self):

        page = 1
        photo_count = 0
        photos_seen = 0
        while page > 0:
            self.info("loading page "+str(page))
            
            photos = self.photos_recentlyUpdated(per_page='50', page=page, min_date=self.min_date)

            if photos.attrib['stat'] != 'ok':
                print "bad stat", photos.attrib['stat']
                sys.exit(1)
                
            photos = photos.find('photos')
            
            if page == 1:
                photo_count = int(photos.attrib['total'])
                self.info("photo count: "+str(photo_count))
                if photo_count == 0:
                    page = 0
                    self.info("no new photos")
                    continue # nothing to do 
                    
            if photos.attrib['page'] == photos.attrib['pages']:
                # last page of results
                page = 0
            else:
                page += 1

            for p in photos.findall('photo'):
                if self.options.ids_only:
                    print p.attrib['id']
                    continue
                
                photo = Photo(self, id=p.attrib['id'])

                photos_seen += 1
                self.info("%d / %d : %s - %s" % (photos_seen, photo_count, photo.vals['title'], photo.vals['description']))

                photo.save()

        if photo_count > 0:
            self.update_web_pages()
            
        self.set_last_updated_timestamp()
        
        return photo_count

if __name__ == "__main__":
    p = PicBackFlick()
    p.get_recent_photos()
    
        
        
        
        
        
