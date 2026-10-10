import os
import re
import subprocess
import tempfile
import time
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
RUN = os.path.join(SCRIPTS, "_run.sh")
LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4} \[(\w+)\] (.*)$")


class ScriptTestCase(unittest.TestCase):
    """Kör skalskripten med /bin/bash som "python" och små skalskript som main.py osv. i en
    temporär projektmapp. Ingen riktig Python-kod, databas eller API rörs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.logs = os.path.join(self.dir, "logs")

    def fake(self, name, body):
        with open(os.path.join(self.dir, name), "w") as f:
            f.write(body)

    def env(self, **extra):
        env = {"PATH": "/usr/bin:/bin", "HOME": self.dir,          # ungefär som cron
               "ODDISY_DIR": self.dir, "ODDISY_PYTHON": "/bin/bash",
               "ODDISY_TIMEOUT": "30", "ODDISY_KILL_GRACE": "1"}
        env.update(extra)
        return env

    def run_script(self, args, timeout=20, **extra):
        return subprocess.run(args, env=self.env(**extra), capture_output=True, text=True, timeout=timeout)

    def start(self, args, **extra):
        p = subprocess.Popen(args, env=self.env(**extra), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        return p

    def log(self, name="main"):
        path = os.path.join(self.logs, f"{name}.log")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return f.read().splitlines()

    def messages(self, name="main"):
        result = []
        for line in self.log(name):
            m = LINE.match(line)
            self.assertIsNotNone(m, f"rad utan tidsstämpel: {line!r}")
            self.assertEqual(m.group(1), name)
            result.append(m.group(2))
        return result

    def lock(self, name="main"):
        return os.path.join(self.logs, f"{name}.lock")

    def assert_child_dead(self):
        """main.py-fejkarna som startar "sleep 30 &" skriver barnets pid till child.pid."""
        with open(os.path.join(self.dir, "child.pid")) as f:
            child = int(f.read())
        def dead():
            try:
                os.kill(child, 0)
                return False
            except ProcessLookupError:
                return True
        self.assertTrue(self.wait_for(dead, 5), "barnprocessen lever kvar")

    def wait_for(self, condition, seconds=10):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if condition():
                return True
            time.sleep(0.05)
        return False


class TestRun(ScriptTestCase):
    def test_output_logged_with_timestamps_and_exit_code(self):
        self.fake("main.py", 'echo "hej"\necho "fel" >&2\nprintf "sista utan radbrytning"\nexit 0\n')
        p = self.run_script([RUN, "main", "main.py"])
        self.assertEqual(p.returncode, 0)
        self.assertEqual((p.stdout, p.stderr), ("", ""))          # inget till cron
        msgs = self.messages()
        self.assertTrue(msgs[0].startswith("Startar main.py (pid "))
        self.assertEqual(msgs[1:4], ["hej", "fel", "sista utan radbrytning"])
        self.assertRegex(msgs[-1], r"^Klar på \d+ s\.$")
        self.assertFalse(os.path.exists(self.lock()))

    def test_runs_in_project_dir(self):
        self.fake("main.py", "pwd\n")
        self.run_script([RUN, "main", "main.py"])
        self.assertEqual(os.path.realpath(self.messages()[1]), os.path.realpath(self.dir))

    def test_appends_between_runs(self):
        self.fake("main.py", "echo körning\n")
        self.run_script([RUN, "main", "main.py"])
        self.run_script([RUN, "main", "main.py"])
        self.assertEqual(self.messages().count("körning"), 2)

    def test_exit_code_passed_through(self):
        self.fake("main.py", 'echo "Avbryter: Kreditskydd (reserv)" >&2\nexit 2\n')
        p = self.run_script([RUN, "main", "main.py"])
        self.assertEqual(p.returncode, 2)
        self.assertIn("Avbryter: Kreditskydd (reserv)", self.messages())
        self.assertIn("Kreditskyddet?", self.messages()[-1])
        self.fake("main.py", "exit 5\n")
        self.assertEqual(self.run_script([RUN, "main", "main.py"]).returncode, 5)
        self.assertRegex(self.messages()[-1], r"^Slut med felkod 5")

    def test_missing_python(self):
        p = self.run_script([RUN, "main", "main.py"], ODDISY_PYTHON=os.path.join(self.dir, "finns_inte"))
        self.assertEqual(p.returncode, 127)
        self.assertTrue(self.messages()[-1].startswith("Fel: hittar inte"))
        self.assertFalse(os.path.exists(self.lock()))

    def test_usage(self):
        p = self.run_script([RUN])
        self.assertEqual(p.returncode, 64)

    def test_wrappers_run_right_script_and_log(self):
        for name in ("main", "clv", "results"):
            self.fake(f"{name}.py", f"echo jag är {name}\n")
        for name in ("main", "clv", "results"):
            with self.subTest(name):
                p = self.run_script([os.path.join(SCRIPTS, f"run_{name}.sh")])
                self.assertEqual(p.returncode, 0)
                self.assertIn(f"jag är {name}", self.messages(name))

    def test_wrappers_use_absolute_project_paths_by_default(self):
        with open(RUN) as f:
            text = f.read()
        self.assertIn('DIR="${ODDISY_DIR:-/Users/simonsayadi/Dev/oddisy}"', text)
        self.assertIn('PYTHON="${ODDISY_PYTHON:-/Users/simonsayadi/Dev/oddisy/venv/bin/python}"', text)
        self.assertIn('TIMEOUT="${ODDISY_TIMEOUT:-600}"', text)


class TestLock(ScriptTestCase):
    def test_second_run_skipped_while_first_running(self):
        self.fake("main.py", "echo startad\nsleep 2\necho färdig\n")
        first = self.start([RUN, "main", "main.py"])
        self.assertTrue(self.wait_for(lambda: "startad" in self.messages()))
        second = self.run_script([RUN, "main", "main.py"])
        self.assertEqual(second.returncode, 0)
        self.assertTrue(any(m.startswith("Hoppar över: förra körningen pågår") for m in self.messages()))
        self.assertEqual(first.wait(timeout=20), 0)
        msgs = self.messages()
        self.assertEqual(msgs.count("startad"), 1)
        self.assertIn("färdig", msgs)
        self.assertFalse(os.path.exists(self.lock()))

    def test_different_scripts_do_not_block_each_other(self):
        self.fake("main.py", "sleep 2\n")
        self.fake("clv.py", "echo clv kör\n")
        first = self.start([RUN, "main", "main.py"])
        self.assertTrue(self.wait_for(lambda: os.path.exists(os.path.join(self.lock(), "pid"))))
        self.assertEqual(self.run_script([RUN, "clv", "clv.py"]).returncode, 0)
        self.assertIn("clv kör", self.messages("clv"))
        first.wait(timeout=20)

    def make_lock(self, pid=None, age_seconds=0):
        os.makedirs(self.lock())
        if pid is not None:
            with open(os.path.join(self.lock(), "pid"), "w") as f:
                f.write(f"{pid}\n")
        if age_seconds:
            old = time.time() - age_seconds
            os.utime(self.lock(), (old, old))

    def dead_pid(self):
        p = subprocess.Popen(["/usr/bin/true"])
        p.wait()
        return p.pid

    def test_stale_lock_with_dead_pid_removed(self):
        self.fake("main.py", "echo körde\n")
        self.make_lock(self.dead_pid())
        p = self.run_script([RUN, "main", "main.py"])
        self.assertEqual(p.returncode, 0)
        msgs = self.messages()
        self.assertIn("Tar bort gammalt lås, processen lever inte längre.", msgs)
        self.assertIn("körde", msgs)
        self.assertFalse(os.path.exists(self.lock()))

    def test_reused_pid_of_other_process_is_stale(self):
        self.fake("main.py", "echo körde\n")
        self.make_lock(os.getpid())              # lever, men är inte _run.sh
        self.run_script([RUN, "main", "main.py"])
        self.assertIn("körde", self.messages())

    def test_lock_without_pid_fresh_is_respected(self):
        self.fake("main.py", "echo körde\n")
        self.make_lock()
        self.run_script([RUN, "main", "main.py"])
        self.assertNotIn("körde", self.messages())
        self.assertTrue(os.path.isdir(self.lock()))              # inte vårt lås, rörs inte

    def test_lock_without_pid_old_is_removed(self):
        self.fake("main.py", "echo körde\n")
        self.make_lock(age_seconds=300)
        self.run_script([RUN, "main", "main.py"])
        self.assertIn("körde", self.messages())

    def test_lock_removed_when_killed(self):
        self.fake("main.py", "sleep 30 & echo $! > child.pid\necho startad\nwait\n")
        p = self.start([RUN, "main", "main.py"])
        self.assertTrue(self.wait_for(lambda: "startad" in self.messages()))
        p.terminate()
        self.assertEqual(p.wait(timeout=10), 143)
        self.assertFalse(os.path.exists(self.lock()))
        self.assertIn("Avbruten av signal.", self.messages())
        self.assert_child_dead()


class TestTimeout(ScriptTestCase):
    def test_timeout_stops_run(self):
        self.fake("main.py", "sleep 30 & echo $! > child.pid\necho startad\nwait\necho borde inte synas\n")
        started = time.time()
        p = self.run_script([RUN, "main", "main.py"], ODDISY_TIMEOUT="1")
        self.assertLess(time.time() - started, 10)
        self.assertEqual(p.returncode, 124)
        msgs = self.messages()
        self.assertIn("startad", msgs)
        self.assertNotIn("borde inte synas", msgs)
        self.assertEqual(msgs[-1], "TIMEOUT: main.py stoppades efter 1 s.")
        self.assertFalse(os.path.exists(self.lock()))
        self.assert_child_dead()

    def test_kill_after_grace_when_term_ignored(self):
        self.fake("main.py", "trap '' TERM\nsleep 30 & echo $! > child.pid\necho startad\nwait; sleep 30\n")
        started = time.time()
        p = self.run_script([RUN, "main", "main.py"], ODDISY_TIMEOUT="1", ODDISY_KILL_GRACE="1")
        self.assertLess(time.time() - started, 10)
        self.assertEqual(p.returncode, 124)
        self.assert_child_dead()

    def test_watchdog_does_not_linger(self):
        # Utan städning skulle watchdogens sleep hålla stdout öppen och cron vänta i 30 s
        self.fake("main.py", "echo snabb\n")
        started = time.time()
        p = self.run_script([RUN, "main", "main.py"], ODDISY_TIMEOUT="30")
        self.assertEqual(p.returncode, 0)
        self.assertLess(time.time() - started, 5)


class TestRotation(ScriptTestCase):
    def test_rotates_over_1mb_and_overwrites_old(self):
        os.makedirs(self.logs)
        big = os.path.join(self.logs, "main.log")
        with open(big, "w") as f:
            f.write("x" * (1024 * 1024 + 1))
        with open(big + ".1", "w") as f:
            f.write("äldsta\n")
        self.fake("main.py", "echo ny\n")
        self.run_script([RUN, "main", "main.py"])
        self.assertEqual(os.path.getsize(big + ".1"), 1024 * 1024 + 1)
        self.assertIn("ny", self.messages())
        self.assertLess(os.path.getsize(big), 1000)

    def test_small_log_not_rotated(self):
        os.makedirs(self.logs)
        with open(os.path.join(self.logs, "main.log"), "w") as f:
            f.write("x" * 1024 * 1024)                  # exakt 1 MB, roteras inte
        self.fake("main.py", "echo ny\n")
        self.run_script([RUN, "main", "main.py"])
        self.assertFalse(os.path.exists(os.path.join(self.logs, "main.log.1")))


if __name__ == "__main__":
    unittest.main()
