#!/usr/bin/env python3

import os
import sys
import json
import getopt
import collections
import urllib.error
import urllib.request


SCRIPT = os.path.basename(__file__)
ENDPOINT = "https://registry-test.cilogon.org/registry/"
OSG_CO_ID = 8


_usage = f"""\
usage: [PASS=...] {SCRIPT} [OPTIONS]

OPTIONS:
  -u USER[:PASS]      specify USER and optionally PASS on command line
  -c OSG_CO_ID        specify OSG CO ID (default = {OSG_CO_ID})
  -d passfd           specify open fd to read PASS
  -f passfile         specify path to file to open and read PASS
  -e ENDPOINT         specify REST endpoint
                        (default = {ENDPOINT})
  -o outfile          specify output file (default: write to stdout)
  -h                  display this help text

PASS for USER is taken from the first of:
  1. -u USER:PASS
  2. -d passfd (read from fd)
  3. -f passfile (read from file)
  4. read from $PASS env var
"""

def usage(msg=None):
    if msg:
        print(msg + "\n", file=sys.stderr)

    print(_usage, file=sys.stderr)
    sys.exit()


class Options:
    endpoint = ENDPOINT
    user = "co_8.project_script"
    osg_co_id = OSG_CO_ID
    outfile = None
    authstr = None


options = Options()


def getpw(user, passfd, passfile):
    if ':' in user:
        user, pw = user.split(':', 1)
    elif passfd is not None:
        pw = os.fdopen(passfd).readline().rstrip('\n')
    elif passfile is not None:
        pw = open(passfile).readline().rstrip('\n')
    elif 'PASS' in os.environ:
        pw = os.environ['PASS']
    else:
        usage("PASS required")
    return user, pw


def mkauthstr(user, passwd):
    from base64 import encodebytes
    raw_authstr = f'{user}:{passwd}'
    return encodebytes(raw_authstr.encode()).decode().replace('\n', '')


def mkrequest(target, **kw):
    url = os.path.join(options.endpoint, target)
    if kw:
        url += "?" + "&".join( "{}={}".format(k,v) for k,v in kw.items() )
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {options.authstr}")
    req.get_method = lambda: 'GET'
    return req


def call_api(target, **kw):
    req = mkrequest(target, **kw)
    resp = urllib.request.urlopen(req)
    payload = resp.read()
    return json.loads(payload) if payload else None


def get_osg_co_groups():
    return call_api("co_groups.json", coid=options.osg_co_id)


# primary api calls

def get_co_group_identifiers(gid):
    return call_api("identifiers.json", cogroupid=gid)


def get_co_group_members(gid):
    return call_api("co_group_members.json", cogroupid=gid)


def get_co_person_identifiers(pid):
    return call_api("identifiers.json", copersonid=pid)


# @rorable
# def foo(x): ...
# x | foo -> foo(x)
class rorable:
    def __init__(self, f): self.f = f
    def __call__(self, *a, **kw): return self.f(*a, **kw)
    def __ror__ (self, x): return self.f(x)


def get_datalist(listname):
    def get(data):
        return data[listname] if data else []
    return rorable(get)


# api call results massagers

def get_osg_co_groups__map():
    #print("get_osg_co_groups__map()")
    data = get_osg_co_groups() | get_datalist("CoGroups")
    return { g["Id"]: g["Name"] for g in data }


def co_group_is_ospool(gid):
    #print(f"co_group_is_ospool({gid})")
    data = get_co_group_identifiers(gid) | get_datalist("Identifiers")
    return any( i["Type"] == "ospoolproject" for i in data )


def get_co_group_members__pids(gid):
    #print(f"get_co_group_members__pids({gid})")
    data = get_co_group_members(gid) | get_datalist("CoGroupMembers")
    return [ m["Person"]["Id"] for m in data ]


def get_co_person_osguser(pid):
    #print(f"get_co_person_osguser({pid})")
    data = get_co_person_identifiers(pid) | get_datalist("Identifiers")
    typemap = { i["Type"]: i["Identifier"] for i in data }
    return typemap.get("osguser")


def parse_options(args):
    try:
        ops, args = getopt.getopt(args, 'u:c:d:f:e:o:h')
    except getopt.GetoptError:
        usage()

    if args:
        usage(f"Extra arguments: {repr(args)}")

    passfd = None
    passfile = None

    for op, arg in ops:
        if op == '-h': usage()
        if op == '-u': options.user      = arg
        if op == '-c': options.osg_co_id = int(arg)
        if op == '-d': passfd            = int(arg)
        if op == '-f': passfile          = arg
        if op == '-e': options.endpoint  = arg
        if op == '-o': options.outfile   = arg

    user, passwd = getpw(options.user, passfd, passfile)
    options.authstr = mkauthstr(user, passwd)


def gid_pids_to_osguser_pid_gids(gid_pids, pid_osguser):
    pid_gids = collections.defaultdict(set)

    for gid in gid_pids:
        for pid in gid_pids[gid]:
            if pid_osguser[pid] is not None:
                pid_gids[pid].add(gid)

    return pid_gids


def get_osguser_groups():
    groups = get_osg_co_groups__map()
    ospool_gids = filter(co_group_is_ospool, groups)
    gid_pids = { gid: get_co_group_members__pids(gid) for gid in ospool_gids }
    all_pids = set( pid for gid in gid_pids for pid in gid_pids[gid] )
    pid_osguser = { pid: get_co_person_osguser(pid) for pid in all_pids }
    pid_gids = gid_pids_to_osguser_pid_gids(gid_pids, pid_osguser)

    return { pid_osguser[pid]: sorted(map(groups.get, gids))
             for pid, gids in pid_gids.items() }


def print_usermap_to_file(osguser_groups, file):
    for osguser, groups in sorted(osguser_groups.items()):
        print("* {} {}".format(osguser, ",".join(groups)), file=file)


def print_usermap(osguser_groups):
    if options.outfile:
        with open(options.outfile, "w") as w:
            print_usermap_to_file(osguser_groups, w)
    else:
        print_usermap_to_file(osguser_groups, sys.stdout)


def main(args):
    parse_options(args)

    osguser_groups = get_osguser_groups()
    print_usermap(osguser_groups)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except urllib.error.HTTPError as e:
        print(e, file=sys.stderr)
        sys.exit(1)

