# coding=utf-8
"""InaSAFE Disaster risk tool by Australian Aid - Metadata for generic Impact
function on Building for Classified Hazard.

Contact : ole.moller.nielsen@gmail.com

.. note:: This program is free software; you can redistribute it and/or modify
     it under the terms of the GNU General Public License as published by
     the Free Software Foundation; either version 2 of the License, or
     (at your option) any later version.

"""
from safe.common.utilities import OrderedDict
from safe.utilities.i18n import tr

from safe.defaults import building_type_postprocessor
from safe.impact_functions.impact_function_metadata import (
    ImpactFunctionMetadata)
from safe.new_definitions import (
    layer_mode_classified,
    layer_geometry_polygon,
    layer_geometry_point,
    layer_geometry_raster,
    hazard_all,
    hazard_category_multi_hazard,
    exposure_structure,
    generic_raster_hazard_classes,
    hazard_category_single_hazard
)

__author__ = 'lucernae'
__project_name__ = 'inasafe'
__filename__ = 'metadata_definitions'
__date__ = '23/03/15'
__copyright__ = 'lana.pcfre@gmail.com'


class ClassifiedHazardBuildingMetadata(ImpactFunctionMetadata):
    """Metadata for Classified Hazard Building Impact Function.

    .. versionadded:: 2.1

    We only need to re-implement as_dict(), all other behaviours
    are inherited from the abstract base class.
    """

    @staticmethod
    def as_dict():
        """Return metadata as a dictionary.

        This is a static method. You can use it to get the metadata in
        dictionary format for an impact function.

        :returns: A dictionary representing all the metadata for the
            concrete impact function.
        :rtype: dict
        """
        dict_meta = {
            'id': 'ClassifiedHazardBuildingFunction',
            'name': tr('Classified Hazard Building Function'),
            'impact': tr('Be impacted'),
            'title': tr('Be impacted by each hazard class'),
            'function_type': 'old-style',
            'author': 'Dianne Bencito',
            'date_implemented': 'N/A',
            'overview': tr(
                'To assess the impacts of classified hazards in raster '
                'format on building vector layer.'),
            'detailed_description': tr(
                'This function will use the class from the hazard layer '
                'that has been identified by the user which one is low, '
                'medium, or high from the parameter that user input. '
                'After that, this impact function will calculate the '
                'building will be impacted per each class for class in '
                'the hazard layer. Finally, it will show the result and '
                'the total of building that will be affected for the '
                'hazard given.'),
            'hazard_input': tr(
                'A hazard raster layer where each cell represents the '
                'class of the hazard. There should be 3 classes: e.g. '
                '1, 2, and 3.'),
            'exposure_input': tr(
                'Vector polygon layer which can be extracted from OSM '
                'where each polygon represents the footprint of a '
                'building.'),
            'output': tr(
                'The impact layer will contain all structures that were '
                'exposed to the highest class (3) and a summary table '
                'containing the number of structures in each class.'),
            'actions': tr(
                'Provide details about how many building would likely be '
                'impacted for each hazard class.'),
            'limitations': [tr('The number of classes is three.')],
            'citations': [],
            'layer_requirements': {
                'hazard': {
                    'layer_mode': layer_mode_classified,
                    'layer_geometries': [layer_geometry_raster],
                    'hazard_categories': [
                        hazard_category_multi_hazard,
                        hazard_category_single_hazard
                    ],
                    'hazard_types': hazard_all,
                    'continuous_hazard_units': [],
                    'vector_hazard_classifications': [],
                    'raster_hazard_classifications': [
                        generic_raster_hazard_classes]
                },
                'exposure': {
                    'layer_mode': layer_mode_classified,
                    'layer_geometries': [
                        layer_geometry_point,
                        layer_geometry_polygon
                    ],
                    'exposure_types': [exposure_structure],
                    'exposure_units': []
                }
            },
            # parameters
            'parameters': OrderedDict([
                ('low_hazard_class', 1.0),
                ('medium_hazard_class', 2.0),
                ('high_hazard_class', 3.0),
                ('postprocessors', OrderedDict([('BuildingType',
                                                 building_type_postprocessor())
                                                ]))
            ])
        }
        return dict_meta
