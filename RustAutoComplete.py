import os
import time
import sublime
import sublime_plugin
import re
import platform
import subprocess
from subprocess import Popen, PIPE


class Racer:
    def load(self):
        package_settings = sublime.load_settings(
            "RustAutoComplete.sublime-settings")
        package_settings.add_on_change("racer", self.reload)
        package_settings.add_on_change("search_paths", self.reload)

        self.racer_bin = package_settings.get("racer", "racer")
        search_paths = package_settings.get("search_paths", [])
        self.package_settings = package_settings

        # Copy the system environment and add the source search
        # paths for racer to it
        env = os.environ.copy()
        expanded_search_paths = self.get_rust_src_paths(search_paths)

        # Don't even try to load racer. It'll just quit on us.
        if not expanded_search_paths:
            sublime.status_message('No valid Rust source path found for Racer')
        else:
            env['RUST_SRC_PATH'] = os.pathsep.join(expanded_search_paths)

            # Run racer
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            try:
                # We use the shell if the path isn't an absolute one
                self.process = Popen(
                    [self.racer_bin, "-i", "tab-text", "daemon"],
                    stdin=PIPE, stdout=PIPE, stderr=PIPE,
                    bufsize=0, env=env, startupinfo=startupinfo,
                    shell=not os.access(self.racer_bin, os.X_OK))
            except:
                pass

            # Let racer catch up
            time.sleep(0.2)
            if not self.check_racer_process():
                sublime.status_message(
                    'Failed to start Racer. Check the console for more info')

    def get_rust_src_paths(self, search_paths):
        sys_src_paths = [
            os.path.expanduser(path) for path in search_paths]

        env = os.environ.copy()
        if 'RUST_SRC_PATH' in env:
            sys_src_paths.extend(env['RUST_SRC_PATH'].split(os.pathsep))
        else:
            # Append the active rustup toolchain path to the search
            # paths if it's not already there (`get_rust_src_paths` should
            # fix it up to point to the source if the rust-src component
            # is installed for the active toolchain).
            # This isn't the best course of action for everybody because
            # cargo can invoke another toolchain, so we use it only if
            # RUST_SRC_PATH isn't available.
            try:
                rustup_active_rustc = subprocess.check_output(
                    ['rustup', 'which', 'rustc'],
                    shell=not os.access(
                        'rustup', os.X_OK)).decode('utf-8')[:-1]
                rustup_active_rustc = os.path.split(
                    os.path.dirname(
                        os.path.expanduser(rustup_active_rustc)))[0]
                if not any(
                        os.path.abspath(p).startswith(rustup_active_rustc)
                        for p in sys_src_paths):
                    sys_src_paths.append(rustup_active_rustc)
            except:
                pass

        # fix for Mac
        # copy from https://github.com/int3h/SublimeFixMacPath
        if platform.system() == 'Darwin':
            # Execute command with original environ. Otherwise, our changes to
            # the PATH propogate down to the shell we spawn, which re-adds the
            # system path & returns it, leading to duplicate values.
            sys_src_path = subprocess.check_output(
                "TERM=ansi CLICOLOR=\"\" SUBLIME=1 /usr/bin/login -fqpl $USER $SHELL -l -c 'TERM=ansi CLICOLOR=\"\" SUBLIME=1 printf \"%s\" \"$RUST_SRC_PATH\"'",
                stdout=PIPE, shell=True, env={}).decode('utf-8')

            # Remove ANSI control characters
            # (see: http://www.commandlinefu.com/commands/view/3584/remove-color-codes-special-characters-with-sed)
            sys_src_path = re.sub(
                r'\x1B\[([0-9]{1,2}(;[0-9]{1,2})?)?[m|K]', '', sys_src_path)

            sys_src_paths += sys_src_path.strip().rstrip(
                os.pathsep).split(os.pathsep)

        def fixup_rust_src_dir(path, left_depth):
            if left_depth <= 0:
                return None

            def is_rust_src_path(p):
                return os.path.split(p)[1] == 'src' and \
                    os.path.isdir(os.path.join(p, 'rustc'))

            if is_rust_src_path(path):
                return path

            for d in filter(lambda p: os.path.isdir(os.path.join(path, p)),
                            os.listdir(path)):
                fp = os.path.join(path, d)
                if is_rust_src_path(fp):
                    return fp

                fp = fixup_rust_src_dir(fp, left_depth - 1)
                if fp:
                    return fp
            return None

        # This is dirty, but we need to do it if people
        # configure RUST_SRC_PATH to a parent directory
        # which is also named `src'.
        for i, path in enumerate(sys_src_paths):
            src_path = fixup_rust_src_dir(path, 5)
            if not src_path:
                del sys_src_paths[i]
            elif src_path != path:
                print(
                    "Converting invalid rust source path `{}' to `{}'".format(
                        path, src_path))
                sys_src_paths[i] = src_path

        return sys_src_paths

    def unload(self):
        if hasattr(self, 'package_settings'):
            self.package_settings.clear_on_change("racer")
            self.package_settings.clear_on_change("search_paths")
            del self.package_settings
        if hasattr(self, 'process'):
            self.process.kill()
            del self.process

    def reload(self):
        self.unload()
        self.load()
        if not self.check_racer_process():
            sublime.error_message(
                'Failed to start Racer. Check the console for more info')

    def run_command(self, args, content):
        if not self.process:
            return None

        self.process.stdin.write(u'\t'.join(args).encode('utf-8'))
        self.process.stdin.write(u'\n'.encode('utf-8'))
        self.process.stdin.write(str(content).encode('utf-8'))
        self.process.stdin.write(u'\x04'.encode('utf-8'))
        # print("input ", '\t'.join(args), '\n', content)

        if not self.check_racer_process():
            sublime.error_message(
                "Racer quit unexpectedly. See console for more info.")
            self.reload()
            return []

        results = []

        while True:
            line = self.process.stdout.readline().decode('utf-8')
            parts = line.rstrip().split('\t')
            if parts[0] == 'MATCH':
                if len(parts) == 7:  # without snippet
                    parts.insert(2, None)
                results.append(Result(parts))
                continue
            if parts[0] == 'END':
                break

        return results

    def complete_with_snippet(self, row, col, filename, content):
        args = [
            "complete-with-snippet", str(row), str(col),
            self.fixup_filename(filename), '-']
        return self.run_command(args, content)

    def find_definition(self, row, col, filename, content):
        args = [
            "find-definition", str(row), str(col),
            self.fixup_filename(filename), '-']
        return self.run_command(args, content)

    def fixup_filename(self, fn):
        return '-' if fn is None else fn

    def check_racer_process(self):
        if hasattr(self, 'process'):
            returncode = self.process.poll()
            if returncode is None:
                return True

            print("Racer failed with {}".format(returncode))
            print("    stdout: {}".format(
                self.process.stdout.read().decode('utf-8').replace('\r', '')))
            print("    stderr: {}".format(
                self.process.stderr.read().decode('utf-8').replace('\r', '')))
            self.process.kill()
            del self.process

        return False


racer = Racer()
plugin_loaded = racer.load
plugin_unloaded = racer.unload


class Result:
    def __init__(self, parts):
        self.completion = parts[1]
        self.snippet = parts[2]
        self.row = int(parts[3])
        self.column = int(parts[4])
        self.path = parts[5]
        self.type = parts[6]
        self.context = parts[7]


class RustAutocomplete(sublime_plugin.EventListener):
    def on_query_completions(self, view, prefix, locations):
        # Check if this is a Rust source file. This check
        # relies on the Rust syntax formatting extension
        # being installed - https://github.com/jhasse/sublime-rust
        if not view.match_selector(locations[0], "source.rust"):
            return None

        row, col = view.rowcol(locations[0])
        region = sublime.Region(0, view.size())
        content = view.substr(region)
        raw_results = racer.complete_with_snippet(
            row+1, col, view.file_name(), content)

        results = []
        lalign = 0
        ralign = 0
        for result in raw_results:
            result.middle = "{0} ({1})".format(
                result.type, os.path.basename(result.path))
            lalign = max(lalign, len(result.completion)+len(result.middle))
            ralign = max(ralign, len(result.context))

        for result in raw_results:
            result = "{0} {1:>{3}} : {2:{4}}".format(
                result.completion,
                result.middle,
                result.context,
                lalign - len(result.completion),
                ralign), result.snippet
            results.append(result)
        if results:
            return (list(results),
                    sublime.INHIBIT_WORD_COMPLETIONS |
                    sublime.INHIBIT_EXPLICIT_COMPLETIONS)


class RustGotoDefinitionCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        row, col = self.view.rowcol(self.view.sel()[0].begin())
        region = sublime.Region(0, self.view.size())
        content = self.view.substr(region)
        results = racer.find_definition(
            row+1, col, self.view.file_name(), content)
        if len(results) == 1:
            result = results[0]
            path = result.path
            # On Windows the racer will return the paths without the drive
            # letter and we need the letter for the open_file to work.
            if sublime.platform() == 'windows' and not re.compile(
                    '^\w\:').match(path):
                path = 'c:' + path
            encoded_path = "{0}:{1}:{2}".format(
                path, result.row, result.column+1)
            self.view.window().open_file(
                encoded_path, sublime.ENCODED_POSITION)
