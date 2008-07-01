#!/usr/bin/python -tt

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
# copyright 2006 Duke University
# author seth vidal

# sync all or the newest packages from a repo to the local path
# TODO:
#     have it print out list of changes
#     make it work with mirrorlists (silly, really)
#     man page/more useful docs
#     deal nicely with a package changing but not changing names (ie: replacement)
#     maybe have it iterate the dir, if it exists, and delete files not listed
#     in a repo

# criteria
# if a package is not the same and smaller then reget it
# if a package is not the same and larger, delete it and get it again
# always replace metadata files if they're not the same.



import os
import sys
import shutil

from optparse import OptionParser
from urlparse import urljoin

import yum
import yum.Errors
from yum.misc import getCacheDir
from yum.constants import *
from yum.packages import parsePackages
from yum.packageSack import ListPackageSack
import rpmUtils.arch
import logging

# for yum 2.4.X compat
def sortPkgObj(pkg1 ,pkg2):
    """sorts a list of yum package objects by name"""
    if pkg1.name > pkg2.name:
        return 1
    elif pkg1.name == pkg2.name:
        return 0
    else:
        return -1
        
class RepoSync(yum.YumBase):
    def __init__(self, opts):
        yum.YumBase.__init__(self)
        self.logger = logging.getLogger('yum.verbose.reposync')
        self.opts = opts

def parseArgs():
    usage = """
    Reposync is used to synchronize a remote yum repository to a local 
    directory using yum to retrieve the packages.
    
    %s [options]
    """ % sys.argv[0]

    parser = OptionParser(usage=usage)
    parser.add_option("-c", "--config", default='/etc/yum.conf',
        help='config file to use (defaults to /etc/yum.conf)')
    parser.add_option("-a", "--arch", default=None,
        help='act as if running the specified arch (default: current arch, note: does not override $releasever)')
    parser.add_option("-r", "--repoid", default=[], action='append',
        help="specify repo ids to query, can be specified multiple times (default is all enabled)")
    parser.add_option("-t", "--tempcache", default=False, action="store_true", 
        help="Use a temp dir for storing/accessing yum-cache")
    parser.add_option("-p", "--download_path", dest='destdir', 
        default=os.getcwd(), help="Path to download packages to: defaults to current dir")
    parser.add_option("-g", "--gpgcheck", default=False, action="store_true",
        help="Remove packages that fail GPG signature checking after downloading")
    parser.add_option("-u", "--urls", default=False, action="store_true", 
        help="Just list urls of what would be downloaded, don't download")
    parser.add_option("-n", "--newest-only", dest='newest', default=False, action="store_true", 
        help="Download only newest packages per-repo")
    parser.add_option("-q", "--quiet", default=False, action="store_true", 
        help="Output as little as possible")
        
    (opts, args) = parser.parse_args()
    return (opts, args)


def main():
    (opts, junk) = parseArgs()
    
    if not os.path.exists(opts.destdir) and not opts.urls:
        try:
            os.makedirs(opts.destdir)
        except OSError, e:
            print >> sys.stderr, "Error: Cannot create destination dir %s" % opts.destdir
            sys.exit(1)
    
    if not os.access(opts.destdir, os.W_OK) and not opts.urls:
        print >> sys.stderr, "Error: Cannot write to  destination dir %s" % opts.destdir
        sys.exit(1)
        
    my = RepoSync(opts=opts)
    my.doConfigSetup(fn=opts.config, init_plugins=True)
    
    # do the happy tmpdir thing if we're not root
    if os.geteuid() != 0 or opts.tempcache:
        cachedir = getCacheDir()
        if cachedir is None:
            print >> sys.stderr, "Error: Could not make cachedir, exiting"
            sys.exit(50)
            
        my.repos.setCacheDir(cachedir)

    if len(opts.repoid) > 0:
        myrepos = []
        
        # find the ones we want
        for glob in opts.repoid:
            myrepos.extend(my.repos.findRepos(glob))
        
        # disable them all
        for repo in my.repos.repos.values():
            repo.disable()
        
        # enable the ones we like
        for repo in myrepos:
            repo.enable()

    my.doRpmDBSetup()
    my.doRepoSetup()
    my.doSackSetup(rpmUtils.arch.getArchList(opts.arch))
    
    download_list = []
    

    for repo in my.repos.listEnabled():
        local_repo_path = opts.destdir + '/' + repo.id
            
        reposack = ListPackageSack(my.pkgSack.returnPackages(repoid=repo.id))
            
        if opts.newest:
            download_list = reposack.returnNewestByNameArch()
        else:
            download_list = list(reposack)
        
        download_list.sort(sortPkgObj)
        for pkg in download_list:
            repo = my.repos.getRepo(pkg.repoid)
            remote = pkg.returnSimple('relativepath')
            local = local_repo_path + '/' + remote
            localdir = os.path.dirname(local)
            if not os.path.exists(localdir):
                os.makedirs(localdir)

            if (os.path.exists(local) and 
                str(os.path.getsize(local)) == pkg.returnSimple('packagesize')):
                
                if not opts.quiet:
                    my.logger.error("%s already exists and appears to be complete" % local)
                continue
    
            if opts.urls:
                url = urljoin(repo.urls[0],remote)
                print '%s' % url
                continue
    
            # make sure the repo subdir is here before we go on.
            if not os.path.exists(local_repo_path):
                try:
                    os.makedirs(local_repo_path)
                except IOError, e:
                    my.logger.error("Could not make repo subdir: %s" % e)
                    my.closeRpmDB()
                    sys.exit(1)
            
            # Disable cache otherwise things won't download            
            repo.cache = 0
            if not opts.quiet:
                my.logger.info( 'Downloading %s' % os.path.basename(remote))
            pkg.localpath = local # Hack: to set the localpath we want.
            try:
                path = repo.getPackage(pkg)
            except yum.Errors.RepoError, e:
                my.logger.error("Could not retrieve package %s. Error was %s" % (pkg, str(e))
                continue
                
            if opts.gpgcheck:
                result, error = my.sigCheckPkg(pkg)
                if result != 0:
                    if result == 1:
                        my.logger.warning('Removing %s, due to missing GPG key.' % os.path.basename(remote))
                    elif result == 2:
                        my.logger.warning('Removing %s due to failed signature check.' % os.path.basename(remote))
                    else:
                        my.logger.warning('Removing %s due to failed signature check: %s' % (os.path.basename(remote), error))
                    os.unlink(path)
                    continue

            if not os.path.exists(local) or not os.path.samefile(path, local):
                shutil.copy2(path, local)

    my.closeRpmDB()

if __name__ == "__main__":
    main()
    
