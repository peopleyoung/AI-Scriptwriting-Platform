"""Single-process MVP worker; database job records survive process restarts."""
import logging
from queue import Queue, Empty
from threading import Event, Thread, Lock


class GenerationWorker:
    def __init__(self, callback):
        self.callback = callback
        self.queue = Queue()
        self.stopping = Event()
        self.execution_lock = Lock()
        self.thread = Thread(target=self._loop, name="mujian-generation-worker", daemon=True)

    def start(self):
        self.thread.start()

    def enqueue(self, job_id: str):
        self.queue.put(job_id)

    def _loop(self):
        while not self.stopping.is_set():
            try:
                job_id = self.queue.get(timeout=0.2)
            except Empty:
                continue
            try:
                with self.execution_lock:
                    self.callback(job_id)
            except Exception:
                # Keep processing other jobs; never log provider payloads or secrets.
                logging.getLogger(__name__).error("Generation worker could not persist a job result")
            finally:
                self.queue.task_done()

    def stop(self):
        self.stopping.set()
        self.thread.join(timeout=2)
