from __future__ import absolute_import, print_function

import os
import time
import pytest
import subprocess
import signal

from os.path import abspath, basename, dirname, exists, join, relpath, split, splitext

from tests.plugins.utils import trace, info, fail, ok, red, warn, write, yellow, white
from tests.plugins.image_diff import image_diff
from tests.plugins.phantomjs_screenshot import get_phantomjs_screenshot

from .collect_examples import example_dir
from .utils import deal_with_output_cells


@pytest.mark.examples
def test_file_examples(file_example, example, diff, log_file):
    if pytest.config.option.verbose:
        print()

    if example.is_skip:
        pytest.skip("skipping %s" % example.relpath)

    html_file = "%s.html" % example.path_no_ext
    url = 'file://' + html_file

    (status, duration, out, err) = _run_example(example)
    info("Example run in %s" % white("%.3fs" % duration))

    for line in out.split("\n"):
        if len(line) == 0 or line.startswith("Wrote "):
            continue
        info(line, label="PY")

    for line in err.split("\n"):
        if len(line) == 0:
            continue
        warn(line, label="PY")

    assert status != "timeout", "%s timed out" % example.relpath
    assert status == 0, "%s failed to run (exit code %s)" % (example.relpath, status)

    if example.no_js:
        warn("skipping bokehjs for %s" % example.relpath)
    else:
        _assert_snapshot(example, url, 'file', diff)

        if example.no_diff:
            warn("skipping image diff for %s" % example.relpath)
        else:
            _get_pdiff(example)


### {{{ THIS IS BROKEN and all examples are skipped in examples.yaml
@pytest.mark.examples
def test_server_examples(server_example, example, bokeh_server, diff, log_file):
    if pytest.config.option.verbose:
        print()

    if example.is_skip:
        pytest.skip("skipping %s" % example.relpath)

    # Note this is currently broken - server uses random sessions but we're
    # calling for "default" here - this has been broken for a while.
    # https://github.com/bokeh/bokeh/issues/3897
    url = '%s/?bokeh-session-id=%s' % (bokeh_server, example.name)
    assert _run_example(example) == 0, 'Example did not run'

    if example.no_js:
        warn("skipping bokehjs for %s" % example.relpath)
    else:
        _assert_snapshot(example, url, 'server', diff)

        if example.no_diff:
            warn("skipping image diff for %s" % example.relpath)
        else:
            _get_pdiff(example)


@pytest.mark.examples
def test_notebook_examples(notebook_example, example, jupyter_notebook, diff):
    if pytest.config.option.verbose:
        print()

    if example.is_skip:
        pytest.skip("skipping %s" % example.relpath)

    notebook_port = pytest.config.option.notebook_port
    url_path = join(*_get_path_parts(abspath(example.path)))
    url = 'http://localhost:%d/notebooks/%s' % (notebook_port, url_path)
    assert deal_with_output_cells(example.path), 'Notebook failed'
    _assert_snapshot(example, url, 'notebook', diff)
    if not example.no_diff and diff:
        _get_pdiff(example)
# }}}

def _get_pdiff(example):
    img_path, ref_path, diff_path = example.img_path, example.ref_path, example.diff_path
    trace("generated image: " + img_path)

    ref = example.fetch_ref()

    if not ref:
        warn("reference image %s doesn't exist" % example.ref_url)
    else:
        ref_dir = dirname(ref_path)
        if not exists(ref_dir):
            os.makedirs(ref_dir)

        with open(ref_path, "wb") as f:
            f.write(ref)

        trace("saved reference: " + ref_path)

        example.pixels = image_diff(diff_path, img_path, ref_path)
        if example.pixels != 0:
            comment = "dimensions don't match" if example.pixels == -1 else white("%.02f%%" % example.pixels) + " of pixels"
            warn("generated and reference images differ: %s" % comment)
        else:
            ok("generated and reference images match")


def _get_path_parts(path):
    parts = []
    while True:
        newpath, tail = split(path)
        parts.append(tail)
        path = newpath
        if tail == 'examples':
            break
    parts.reverse()
    return parts


def _print_phantomjs_output(result):
    errors = result['errors']
    messages = result['messages']
    resources = result['resources']

    for message in messages:
        msg = message['msg']
        line = message.get('line')
        source = message.get('source')

        if source and line:
            msg = "%s:%s: %s" % (source, line, msg)

        info(msg, label="JS")

    # Process resources
    for resource in resources:
        url = resource['url']
        if url.endswith(".png"):
            ok("%s: %s (%s)" % (url, yellow(resource['status']), resource['statusText']))
        else:
            fail("Resource error:: %s: %s (%s)" % (url, red(resource['status']), resource['statusText']), label="JS")

    # You can have a successful test, and still have errors reported, so not failing here.
    for error in errors:
        fail(error['msg'], label="JS")
        for item in error['trace']:
            file = item['file']
            line = item['line']

            if file and line:
                fail("  %s: %d" % (file, line), label="JS")


def _assert_snapshot(example, url, example_type, diff):
    screenshot_path = example.img_path

    height = 2000 if example_type == 'notebook' else 1000
    wait = 30000

    start = time.time()
    result = get_phantomjs_screenshot(url, screenshot_path, 1000, wait, 1000, height)
    end = time.time()

    info("Example rendered in %s" % white("%.3fs" % (end - start)))

    success = result['success']
    timeout = result['timeout']
    errors = result['errors']
    messages = result['messages']
    resources = result['resources']

    no_errors = len(errors) == 0

    if timeout:
        warn("%s: %s" % (red("TIMEOUT: "), "bokehjs did not finish in %s ms" % wait))

    if pytest.config.option.verbose:
        _print_phantomjs_output(result)

    assert success, "Example failed to load"
    assert no_errors, "Example failed with %d errors" % len(errors)


def _run_example(example):
    code = """\
__file__ = filename = '%s'

import random
random.seed(1)

import numpy as np
np.random.seed(1)

import warnings
warnings.filterwarnings("ignore", ".*", UserWarning, "matplotlib.font_manager")

with open(filename, 'rb') as example:
    exec(compile(example.read(), filename, 'exec'))
""" % example.path

    cmd = ["python", "-c", code]
    cwd = dirname(example.path)

    env = os.environ.copy()
    env['BOKEH_RESOURCES'] = 'relative'
    env['BOKEH_MINIFIED'] = 'false'
    env['BOKEH_BROWSER'] = 'none'

    class Timeout(Exception):
        pass

    def alarm_handler(sig, frame):
        raise Timeout

    signal.signal(signal.SIGALRM, alarm_handler)
    signal.alarm(20 if not example.is_slow else 60)

    start = time.time()
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        status = proc.wait()
    except Timeout:
        proc.kill()
        status = 'timeout'
    finally:
        signal.alarm(0)
    end = time.time()

    out = proc.stdout.read().decode("utf-8")
    err = proc.stderr.read().decode("utf-8")

    return (status, end - start, out, err)
