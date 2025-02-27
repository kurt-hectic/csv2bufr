###############################################################################
#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
###############################################################################

__version__ = '0.1.0'

import argparse
import csv
import hashlib
import json
from io import StringIO, BytesIO
import logging
from typing import Union
import sys

from jsonschema import validate

from eccodes import (codes_bufr_new_from_samples, codes_set_array, codes_set,
                     codes_get_native_type, codes_write, codes_release)

# some 'constants'
SUCCESS = True
NUMBERS = (float, int, complex)
MISSING = ("NA", "NaN", "NAN", "None")

NULLIFY_INVALID = True  # TODO: move to env. variable

# logging
LOGLEVEL = "INFO"  # TODO: change to read in from environment variable

# set format of logger and loglevel
formatter = logging.Formatter("%(asctime)s\t%(name)s\t%(levelname)s\t%(message)s", "%Y-%m-%d %H:%M:%S")  # noqa
ch = logging.StreamHandler()
ch.setFormatter(formatter)
ch.setLevel(LOGLEVEL)

# now logger for this module
LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(LOGLEVEL)
LOGGER.addHandler(ch)


def validate_mapping_dict(mapping_dict: dict) -> bool:
    """
    Validate mapping dictionary

    :param mapping_dict: TODO: describe

    :returns: `bool` of validation result
    """

    file_schema = {
        "type": "object",
        "properties": {
            "inputDelayedDescriptorReplicationFactor": {
                "type": ["array", "null"]
            },
            "sequence": {
                "type": ["array"]
            }
        }
    }
    # now validate
    try:
        validate(mapping_dict, file_schema)
    except Exception as e:
        message = "invalid mapping dictionary"
        LOGGER.error(message)
        raise e
    # now schema for each element in the sequence array
    # TODO: make optional elements optional
    element_schema = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string"
            },
            "value": {
                "type": [
                    "boolean", "object", "array", "number", "string", "null"
                ]
            },
            "column": {
                "type": ["string", "null"]
            },
            "valid-min": {
                 "type": ["number", "null"]
            },
            "valid-max": {
                 "type": ["number", "null"]
            },
            "scale": {
                "type": ["number", "null"]
            },
            "offset": {
                "type": ["number", "null"]
            }
        }
    }

    # now iterate over elements and validate each
    for element in mapping_dict["sequence"]:
        try:
            validate(element, schema=element_schema)
        except Exception as e:
            message = f"invalid element ({e.json_path}) for {element['key']} in mapping file: {e.message}"  # noqa
            LOGGER.error(message)
            raise e
        if (element["scale"] is None) is not (element["offset"] is None):
            message = f"scale and offset should either both be present or both set to missing for {element['key']} in mapping file"  # noqa
            LOGGER.error(message)
            e = ValueError(message)
            raise e

    return SUCCESS


def apply_scaling(value: Union[NUMBERS], element: dict) -> Union[NUMBERS]:
    """
    Apply simple scaling and offsets

    :param value: TODO describe
    :param element: TODO describe

    :returns: scaled value
    """

    if isinstance(value, NUMBERS):
        if None not in [element["scale"], element["offset"]]:
            try:
                value = value * pow(10, element["scale"]) + element["offset"]
            except Exception as e:
                LOGGER.error(e.message)
                raise e
    return value


def validate_value(key: str, value: Union[NUMBERS],
                   valid_min: Union[NUMBERS],
                   valid_max: Union[NUMBERS],
                   nullify_on_fail: bool = False) -> Union[NUMBERS]:
    """
    Check numeric values lie within specified range (if specified)

    :param key: TODO describe
    :param value: TODO describe
    :param valid_min: TODO describe
    :param valid_max: TODO describe
    :param nullify_on_fail: TODO describe

    :returns: validated value
    """

    if value is None:
        return value
    if not isinstance(value, NUMBERS):
        # TODO: add checking against code / flag table here?
        return(value)
    if valid_min is not None:
        if value < valid_min:
            e = ValueError(f"{key}: Value ({value}) < valid min ({valid_min}).")  # noqa
            if nullify_on_fail:
                message = str(e) + " Element set to missing"
                LOGGER.warning(message)
                return None
            else:
                LOGGER.error(str(e))
                raise e
    if valid_max is not None:
        if value > valid_max:
            e = ValueError(f"{key}: Value ({value}) < valid max ({valid_max}).")  # noqa
            if nullify_on_fail:
                message = str(e) + " Element set to missing"
                LOGGER.warning(message)
                return None
            else:
                LOGGER.error(str(e))
                raise e

    return value


def encode(mapping_dict: dict, data_dict: dict) -> BytesIO:
    """
    This is the primary function that does the conversion to BUFR

    :param mapping_dict: dictionary containing eccodes key and mapping to
                         data dict, includes option to specify
                         valid min and max, scale and offset.
    :param data_dict: dictionary containing data values

    :return: BytesIO object containing BUFR message
    """

    # initialise message to be encoded
    bufr_msg = codes_bufr_new_from_samples("BUFR4")

    # set delayed replication factors if present
    if mapping_dict["inputDelayedDescriptorReplicationFactor"] is not None:
        codes_set_array(bufr_msg, "inputDelayedDescriptorReplicationFactor",
                        mapping_dict["inputDelayedDescriptorReplicationFactor"])  # noqa

    # ===================
    # Now encode the data
    # ===================
    for element in mapping_dict["sequence"]:
        key = element["key"]
        value = None
        assert value is None
        if element["value"] is not None:
            value = element["value"]
        elif element["column"] is not None:
            value = data_dict[element["column"]]
        else:
            # change the following to debug or leave as warning?
            LOGGER.debug(f"No value for {key} but included in mapping file, value set to missing")  # noqa
        # now set
        if value is not None:
            LOGGER.debug(f"setting value {value} for element {key}.")
            if isinstance(value, list):
                try:
                    LOGGER.debug("calling codes_set_array")
                    codes_set_array(bufr_msg, key, value)
                except Exception as e:
                    LOGGER.error(f"error calling codes_set_array({bufr_msg}, {key}, {value}): {e}")  # noqa
                    raise e
            else:
                try:
                    LOGGER.debug("calling codes_set")
                    nt = codes_get_native_type(bufr_msg, key)
                    # convert to native type, required as in Malawi data 0
                    # encoded as "0" for some elements.
                    if nt is int and not isinstance(value, int):
                        LOGGER.warning(f"int expected for {key} but received {type(value)} ({value})")  # noqa
                        if isinstance(value, float):
                            value = int(round(value))
                        else:
                            value = int(value)
                        LOGGER.warning(f"value converted to int ({value})")
                    elif nt is float and not isinstance(value, float):
                        LOGGER.warning(f"float expected for {key} but received {type(value)} ({value})")  # noqa
                        value = float(value)
                        LOGGER.warning(f"value converted to float ({value})")
                    else:
                        value = value
                    codes_set(bufr_msg, key, value)
                except Exception as e:
                    LOGGER.error(f"error calling codes_set({bufr_msg}, {key}, {value}): {e}")  # noqa
                    raise e

    # ==============================
    # Message now ready to be packed
    # ==============================
    try:
        codes_set(bufr_msg, "pack", True)
    except Exception as e:
        LOGGER.error(f"error calling codes_set({bufr_msg}, 'pack', True): {e}")
        raise e

    # =======================================================
    # now write to in memory file and return object to caller
    # =======================================================
    try:
        fh = BytesIO()
        codes_write(bufr_msg, fh)
        codes_release(bufr_msg)
        fh.seek(0)
    except Exception as e:
        LOGGER.error(f"error writing to internal BytesIO object, {e}")
        raise e

    # =============================================
    # Return BytesIO object containing BUFR message
    # =============================================
    return fh


def transform(data: str, mappings: dict, station_metadata: dict) -> dict:
    """
    TODO: describe function

    :param data: TODO: describe
    :param mappings: TODO: describe
    :param station_metadata: TODO: describe

    :return: `dict` of BUFR messages
    """

    # validate mappings
    e = validate_mapping_dict(mappings)
    if e is not SUCCESS:
        raise ValueError("Invalid mappings")

    LOGGER.debug("mapping dictionary validated")

    # TODO: add in code to validate station_metadata

    # we may have multiple rows in the file, create list object to return
    # one item per message
    messages = {}
    # now convert data to StringIO object
    fh = StringIO(data)
    # now read csv data and iterate over rows
    reader = csv.reader(fh, delimiter=',', quoting=csv.QUOTE_NONNUMERIC)
    rows_read = 0
    for row in reader:
        if rows_read == 0:
            col_names = row
        else:
            data = row
            data_dict = dict(zip(col_names, data))
            try:
                data_dict = {**data_dict, **station_metadata['data']}
            except Exception as e:
                message = "issue merging station and data dictionaries."
                LOGGER.error(message + str(e))
                raise e
            # Iterate over items to map, perform unit conversions and validate
            for element in mappings["sequence"]:
                value = element["value"]
                column = element["column"]
                # select between "value" and "column" fields.
                if value is not None:
                    value = element["value"]
                elif column is not None:
                    # get column name
                    # make sure column is in data_dict
                    if (column not in data_dict):
                        message = f"column '{column}' not found in data dictionary"  # noqa
                        raise ValueError(message)
                    value = data_dict[column]
                    if value in MISSING:
                        value = None
                    else:
                        value = apply_scaling(value, element)
                else:
                    LOGGER.debug(f"value and column both None for element {element['key']}")  # noqa
                # now validate value
                LOGGER.debug(f"validating value {value} for element {element['key']}")  # noqa
                value = validate_value(element["key"], value,
                                       element["valid-min"],
                                       element["valid-max"],
                                       NULLIFY_INVALID)

                LOGGER.debug(f"value {value} validated for element {element['key']}")  # noqa
                # update data dictionary
                if column is not None:
                    data_dict[column] = value
                LOGGER.debug(f"value {value} updated for element {element['key']}")  # noqa

            # now encode the data (this one line is where the magic happens
            # once the dictionaries have been read in)
            msg = encode(mappings, data_dict)
            key = hashlib.md5(msg.read()).hexdigest()
            LOGGER.debug(key)
            msg.seek(0)
            messages[key] = msg

        rows_read += 1

    LOGGER.info(f"{rows_read - 1} rows read and converted to BUFR")

    return messages


def cli():
    # =============
    # get arguments
    # =============
    parser = argparse.ArgumentParser(description='csv2bufr')

    parser.add_argument("--mapping", dest="mapping", required=True,
                        help="JSON file mapping from CSV to BUFR")
    parser.add_argument("--input", dest="input", required=True,
                        help="CSV file containing data to encode")
    parser.add_argument("--output", dest="output", required=True,
                        help="Name of output file")
    parser.add_argument("--wigos-id", dest="wsi", required=True,
                        help="WIGOS station identifier, hyphen separated. e.g. 0-20000-0-ABCDEF")  # noqa
    parser.add_argument("--fail-on-invalid", dest="invalid", default=True,
                        help="Flag indicating whether to fail on invalid values. If true invalid values are set to missing")  # noqa

    args = parser.parse_args()

    # now set paths from arguments
    csv_file = args.input
    station_metadata_file = f"{args.config}/{args.wsi}.json"
    mappings_file = f"{args.config}/{args.mapping}"
    result = None

    # ===========================
    # now the code to be executed
    # ===========================
    with open(csv_file) as fh1, open(mappings_file) as fh2, open(station_metadata_file) as fh3:  # noqa
        try:
            result = transform(fh1.read(),
                               mappings=json.load(fh2),
                               station_metadata=json.load(fh3))
        except Exception as err:
            LOGGER.error(err)

    # ======================
    # now write data to file
    # ======================
    for item in result:
        filename = f"{args.output}{item}.bufr4"
        with open(filename, "wb") as fh:
            fh.write(result[item].read())

    return 0


if __name__ == '__main__':
    sys.exit(cli())
