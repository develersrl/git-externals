#!/usr/bin/env python

from __future__ import unicode_literals

import os
import os.path
import shutil
import json
import sys
import glob

try:
    from lxml import etree as ET
except ImportError:
    from xml.etree import ElementTree as ET

from cleanup_repo import cleanup
from utils import git, svn, chdir, checkout, SVNError, branches, tags, header,\
    print_msg
from process_externals import unique_externals


def get_externals(repo):
    data = svn('propget', '--xml', '-R', 'svn:externals', repo)

    targets = ET.fromstring(data).findall('target')

    return unique_externals(targets)


def write_extfile(exts, filename='svn_externals'):
    with open(filename, 'wt') as fd:
        json.dump(exts, fd, indent=4)


def extract_repo_name(remote_name):
    if remote_name[0] == '/':
        remote_name = remote_name[1:]

    if remote_name.startswith('svn/'):
        remote_name = remote_name[len('svn/'):]

    if remote_name.startswith('packages/'):
        i = len('packages/') - 1
        return 'packages/' + extract_repo_name(remote_name[i:])

    j = remote_name.find('/')
    if j < 0:
        return remote_name
    return remote_name[:j]


def extract_repo_root(repo):
    output = svn('info', '--xml', repo)

    rootnode = ET.fromstring(output)
    return rootnode.find('./entry/repository/root').text


def get_layout_opts(repo):
    entries = set(svn('ls', repo).splitlines())

    opts = []

    if 'trunk/' in entries:
        opts.append(
            '--trunk=trunk'
        )

    if 'branches/' in entries:
        opts.append(
            '--branches=branches'
        )

    if 'tags/' in entries:
        opts.append(
            '--tags=tags'
        )

    return opts


def remote_rm(remote):
    remotes = set(git('remote').splitlines())
    if remote in remotes:
        git('remote', 'rm', remote)


def gittify_branch(repo, branch_name, obj, svn_server):
    print_msg('Gittifying branch {}'.format(branch_name))
    gittified = set()

    with checkout(branch_name, obj):
        externals = get_externals(os.path.join(svn_server, repo))

        if len(externals) > 0:
            print_msg('Gittifying externals...')
            with chdir('..'):
                for ext in externals:
                    # FIXME: convert blocked externals revision to commit sha with git svn find-rev
                    repo_name = extract_repo_name(ext['location'])
                    gittified_externals = gittify(repo_name, svn_server)
                    gittified.update(gittified_externals)

            write_extfile(externals)
            git('add', 'svn_externals')
            git('commit', '-m', 'gittify: create svn_externals file')

    return gittified


def gittify(repo, svn_server, basename_only=True, ignore_not_found=True):
    # following to check
    if repo[0] == '/':
        repo = repo[1:]

    repo_name = repo
    if basename_only:
        repo_name = os.path.basename(repo)

    gittified = set([repo_name])

    if os.path.exists(repo_name):
        print_msg('{} already gittified'.format(repo_name))
        return gittified

    tmprepo = '{}.tmp'.format(repo_name)

    remote_repo = os.path.join(svn_server, repo)
    try:
        layout_opts = get_layout_opts(remote_repo)
    except SVNError as err:
        if ignore_not_found:
            print_msg('Ignoring error {}'.format(err))
            return set()
        raise

    is_std = len(layout_opts) == 3

    if not os.path.exists(tmprepo):

        # FIXME: handle authors file for mapping SVN users to Git users
        args = ['svn', 'clone', '--prefix=origin/'] + layout_opts + [remote_repo, tmprepo]

        header('Cloning {}'.format(remote_repo))
        print_msg('standard layout: {}'.format(is_std))
        print_msg(' '.join(args))

        git(*args)
        cleanup(tmprepo, False, remote_repo)

        with chdir(tmprepo):
            print_msg('Gittifying branches...')
            for branch in branches():
                if not is_std:
                    subrepo = repo
                elif branch != 'master':
                    subrepo = os.path.join(repo, 'branches', branch)
                else:
                    subrepo = os.path.join(repo, 'trunk')

                # we've already created a local branch for each
                # origin branch with cleanup
                external_gittified = gittify_branch(subrepo, branch, None, svn_server)
                gittified.update(external_gittified)

            print_msg('Gittifying tags...')
            for tag in tags():
                subrepo = os.path.join(repo, 'tags', tag)
                external_gittified = gittify_branch(subrepo, tag, tag, svn_server)

                if len(external_gittified) > 0:
                    gittified.update(external_gittified)
                    git('tag', '-d', tag)
                    git('tag', tag, tag)

                git('branch', '-D', tag)

        # avoid cycles, because it could be created by a previous call
        # from an external
        if not os.path.exists(repo_name):
            print_msg('Cloning into final git repo')
            git('clone', '--bare', tmprepo, repo_name)
            with chdir(repo_name):
                remote_rm('origin')

    return gittified


def remove_tmp_repos():
    for tmp_repo in glob.iglob('*.tmp'):
        shutil.rmtree(tmp_repo)


if __name__ == '__main__':
    for r in sys.argv[1:]:
        root = extract_repo_root(r)
        repo = r[len(root) + 1:]
        gittify(repo, root)

    remove_tmp_repos()