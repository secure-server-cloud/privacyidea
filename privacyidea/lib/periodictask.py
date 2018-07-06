# -*- coding: utf-8 -*-
#  2018-06-25 Friedrich Weber <friedrich.weber@netknights.it>
#             Initial implementation of periodic tasks
#
# This code is free software; you can redistribute it and/or
# modify it under the terms of the GNU AFFERO GENERAL PUBLIC LICENSE
# License as published by the Free Software Foundation; either
# version 3 of the License, or any later version.
#
# This code is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU AFFERO GENERAL PUBLIC LICENSE for more details.
#
# You should have received a copy of the GNU Affero General Public
# License along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#
__doc__ = """This module provides functions to manage periodic tasks in the database,
to determine their next scheduled running time and to run them."""

import logging
from datetime import datetime

from croniter import croniter
from dateutil.tz import tzutc, tzlocal

from privacyidea.lib.error import ServerError, ParameterError
from privacyidea.models import PeriodicTask

log = logging.getLogger(__name__)


def calculate_next_timestamp(ptask, node, interval_tzinfo=None):
    """
    Calculate the timestamp of the next scheduled run of task ``ptask`` on node ``node``.
    We do not check if the task is even scheduled to run on the specified node.
    If the periodic task has no prior run recorded on the specified node, a
    ``ServerError`` is thrown. Malformed cron expressions may throw a
    ``ValueError``.

    :param ptask: Dictionary describing the periodic task, as from ``PeriodicTask.get()``
    :param node: Node on which the periodic task is scheduled
    :type node: unicode
    :param interval_tzinfo: Timezone in which the cron expression should be interpreted. Defaults to local time.
    :type interval_tzinfo: tzinfo
    :return: a timezone-aware (UTC) datetime object
    """
    if interval_tzinfo is None:
        interval_tzinfo = tzlocal()
    if node not in ptask["last_runs"]:
        raise ServerError("No last run on node {!r} recorded for task {!r}".format(node, ptask["name"]))
    timestamp = ptask["last_runs"][node]
    local_timestamp = timestamp.astimezone(interval_tzinfo)
    iterator = croniter(ptask["interval"], local_timestamp)
    next_timestamp = iterator.get_next(datetime)
    # This will again be a timezone-aware datetime, but we return a timezone-aware UTC timestamp
    return next_timestamp.astimezone(tzutc())


def set_periodic_task(name, interval, nodes, taskmodule, options=None, active=True, id=None):
    """
    Set a periodic task configuration. If ``id`` is None, this creates a new database entry.
    Otherwise, an existing entry is overwritten.
    :param name: Unique name of the periodic task
    :type name: unicode
    :param interval: Periodicity as a string in crontab format
    :type interval: unicode
    :param nodes: List of nodes on which this task should be run
    :type nodes: list of unicode
    :param taskmodule: Name of the task module
    :type taskmodule: unicode
    :param options: Additional options for the task module
    :type options: Dictionary mapping unicodes to values that can be converted to unicode or None
    :param active: Flag determining whether the periodic task is active
    :type active: bool
    :param id: ID of the existing entry, or None
    :type id: int or None
    :return: ID of the entry
    """
    periodic_task = PeriodicTask(name, active, interval, nodes, taskmodule, options, id)
    return periodic_task.id


def delete_periodic_task(ptask_id):
    """
    Delete an existing periodic task. If ``ptask_id`` refers to an unknown entry, a ParameterError is raised.
    :param ptask_id: ID of the database entry
    :return: ID of the deleted entry
    """
    periodic_task = PeriodicTask.query.filter_by(id=ptask_id).first()
    if periodic_task is None:
        raise ParameterError("The periodic task with id {!r} does not exist".format(ptask_id))
    return periodic_task.delete()


def enable_periodic_task(ptask_id, enable=True):
    """
    Set the ``active`` flag of an existing periodic task to ``enable``.
    If ``ptask_id`` refers to an unknown entry, a ParameterError is raised.
    :param ptask_id: ID of the database entry
    :param enable: New value of the ``active`` flag
    :return: ID of the database entry
    """
    periodic_task = PeriodicTask.query.filter_by(id=ptask_id).first()
    if periodic_task is None:
        raise ParameterError("The periodic task with id {!r} does not exist".format(ptask_id))
    periodic_task.active = enable
    return periodic_task.save()


def get_periodic_tasks(name=None, node=None, active=None):
    """
    Get a list of all periodic tasks, or of all tasks satisfying a filter criterion.
    :param name: Name of the periodic task
    :type name: unicode
    :param node: Node for which periodic tasks should be collected. This only includes
                 periodic tasks which are scheduled to run on ``node``.
    :type node: unicode
    :param active: This can be used to filter for active or inactive tasks only
    :return: A (possibly empty) list of periodic task dictionaries
    """
    query = PeriodicTask.query
    if name is not None:
        query = query.filter_by(name=name)
    if active is not None:
        query = query.filter_by(active=active)
    entries = query.all()
    result = []
    for entry in entries:
        ptask = entry.get()
        if node is None or node in ptask["nodes"]:
            result.append(ptask)
    return result


def set_periodic_task_last_run(ptask_id, node, last_run_timestamp):
    """
    Write to the database the information that the specified
    periodic task has been run on a node at a given time.
    :param ptask_id: ID of the periodic task. Raises ParameterError if unknown.
    :type ptask_id: int
    :param node: Node name. It is not checked whether the task is scheduled to run on that node!
    :type node: unioode
    :param last_run_timestamp: Timestamp of the last run
    :type last_run_timestamp: timezone-aware datetime object
    """
    periodic_task = PeriodicTask.query.filter_by(id=ptask_id).first()
    if periodic_task is None:
        raise ParameterError("The periodic task with id {!r} does not exist".format(ptask_id))
    utc_last_run = last_run_timestamp.astimezone(tzutc()).replace(tzinfo=None)
    periodic_task.set_last_run(node, utc_last_run)


def get_scheduled_periodic_tasks(node, current_timestamp=None, interval_tzinfo=None):
    """
    Collect all periodic tasks that should be run on a specific node.

    :param node: Node name
    :type node: unicode
    :param current_timestamp: The current timestamp, defaults to the current time
    :type current_timestamp: timezone-aware datetime
    :param interval_tzinfo: timezone in which the crontab expression should be interpreted
    :type interval_tzinfo: tzinfo, defaults to local time
    :return: List of periodic task dictionaries
    """
    active_ptasks = get_periodic_tasks(node=node, active=True)
    if current_timestamp is None:
        current_timestamp = datetime.now(tzutc())
    if current_timestamp.tzinfo is None:
        raise ParameterError(u"expected timezone-aware datetime, got {!r}".format(current_timestamp))
    scheduled_ptasks = []
    log.debug(u"Collecting periodic tasks to run at {!s}".format(current_timestamp.isoformat()))
    for ptask in active_ptasks:
        try:
            next_timestamp = calculate_next_timestamp(ptask, node, interval_tzinfo)
            log.debug(u"Next scheduled run of {!r}: {!s}".format(ptask["name"], next_timestamp.isoformat()))
            if next_timestamp <= current_timestamp:
                log.debug(u"Scheduling periodic task {!r}".format(ptask["name"]))
                scheduled_ptasks.append(ptask)
        except Exception as e:
            log.warning(u"Ignoring periodic task {!r}: {!r}".format(ptask["name"], e))
    return scheduled_ptasks