"""
Test projtools
"""
import numpy as np
import pyproj.exceptions
import pytest

import geoutils as gu
import geoutils.projtools as pt
from geoutils import examples


class TestProjTools:

    landsat_b4_path = examples.get_path("everest_landsat_b4")
    landsat_b4_crop_path = examples.get_path("everest_landsat_b4_cropped")
    landsat_rgb_path = examples.get_path("everest_landsat_rgb")
    aster_dem_path = examples.get_path("exploradores_aster_dem")

    def test_latlon_to_utm(self) -> None:

        # First: Check errors are raised when format is invalid

        # If format is invalid
        with pytest.raises(TypeError):
            pt.latlon_to_utm("1", 100)  # type: ignore
        # If values are outside limits: latitude above or below 90
        with pytest.raises(ValueError):
            pt.latlon_to_utm(91, 0)
        with pytest.raises(ValueError):
            pt.latlon_to_utm(-91, 0)
        # Or longitude above or below 180
        with pytest.raises(ValueError):
            pt.latlon_to_utm(0, -181)
        with pytest.raises(ValueError):
            pt.latlon_to_utm(0, 181)

        # Second: check that the UTM zone is correct
        # Lower left belongs to the zone, so 0, 0 should be in zone 31N
        assert pt.latlon_to_utm(0, 0) == "30N"
        # Test extreme zones
        assert pt.latlon_to_utm(-79, -179) == "1S"
        assert pt.latlon_to_utm(79, -179) == "1N"
        assert pt.latlon_to_utm(-79, 179) == "60S"
        assert pt.latlon_to_utm(79, 179) == "60N"
        # Test some middles zones
        assert pt.latlon_to_utm(1, -59) == "21N"
        assert pt.latlon_to_utm(1, 61) == "41N"
        assert pt.latlon_to_utm(-1, -121) == "10S"
        assert pt.latlon_to_utm(-1, 119) == "50S"

        # Third, check that any floating or integer type works
        assert pt.latlon_to_utm(0.1, 0.1)
        assert pt.latlon_to_utm(np.float32(0.1), np.float32(0.1))
        assert pt.latlon_to_utm(np.int16(0), np.int16(0))

    def test_utm_to_epsg(self) -> None:
        """Check that the EPSG codes derived from UTM zones are correct"""

        # First: Check errors are raised when format is invalid

        # If there isn't 2 digits for the code
        with pytest.raises(pyproj.exceptions.CRSError):
            pt.utm_to_epsg("100N")
        # If type is incorrect
        with pytest.raises(TypeError):
            pt.utm_to_epsg({"utm": "10N"})  # type: ignore
        # If the code digits does not exist
        with pytest.raises(pyproj.exceptions.CRSError):
            pt.utm_to_epsg("61N")
        # If the north-south zone letter is incorrect
        with pytest.raises(pyproj.exceptions.CRSError):
            pt.utm_to_epsg("61E")

        # Second: Check that the EPSG code is correct
        # https://epsg.io/32601
        assert pt.utm_to_epsg("1N") == 32601
        # https://epsg.io/32701
        assert pt.utm_to_epsg("1S") == 32701
        # https://epsg.io/32&660
        assert pt.utm_to_epsg("60N") == 32660
        # https://epsg.io/32760
        assert pt.utm_to_epsg("60S") == 32760

        # Third: Check that different format work: single digit, lower-case
        assert pt.utm_to_epsg("1N") == pt.utm_to_epsg("01N") == pt.utm_to_epsg("01n")
        assert pt.utm_to_epsg("08s") == pt.utm_to_epsg("8S") == pt.utm_to_epsg("08S")

    @pytest.mark.parametrize("example", [landsat_b4_path, aster_dem_path])  # type: ignore
    def test_latlon_reproject(self, example: str) -> None:
        """
        Check that to and from latlon projections are self consistent within tolerated rounding errors
        """

        img = gu.Raster(example)

        # Test on random points
        nsample = 100
        randx = np.random.randint(low=img.bounds.left, high=img.bounds.right, size=(nsample,))
        randy = np.random.randint(low=img.bounds.bottom, high=img.bounds.top, size=(nsample,))

        lat, lon = pt.reproject_to_latlon([randx, randy], img.crs)
        x, y = pt.reproject_from_latlon([lat, lon], img.crs)

        assert np.all(x == randx)
        assert np.all(y == randy)

    def test_merge_bounds(self) -> None:
        """
        Check that merge_bounds and bounds2poly work as expected for all kinds of bounds objects.
        """
        img1 = gu.Raster(self.landsat_b4_path)
        img2 = gu.Raster(self.landsat_b4_crop_path)

        # Check union (default) - with Raster objects
        out_bounds = pt.merge_bounds((img1, img2))
        assert out_bounds[0] == min(img1.bounds.left, img2.bounds.left)
        assert out_bounds[1] == min(img1.bounds.bottom, img2.bounds.bottom)
        assert out_bounds[2] == max(img1.bounds.right, img2.bounds.right)
        assert out_bounds[3] == max(img1.bounds.top, img2.bounds.top)

        # Check intersection - with Raster objects
        out_bounds = pt.merge_bounds((img1, img2), merging_algorithm="intersection")
        assert out_bounds[0] == max(img1.bounds.left, img2.bounds.left)
        assert out_bounds[1] == max(img1.bounds.bottom, img2.bounds.bottom)
        assert out_bounds[2] == min(img1.bounds.right, img2.bounds.right)
        assert out_bounds[3] == min(img1.bounds.top, img2.bounds.top)

        # Check that the results is the same with rio.BoundingBoxes
        out_bounds2 = pt.merge_bounds((img1.bounds, img2.bounds), merging_algorithm="intersection")
        assert out_bounds2 == out_bounds

        # Check that the results is the same with a list
        out_bounds2 = pt.merge_bounds((list(img1.bounds), list(img2.bounds)), merging_algorithm="intersection")
        assert out_bounds2 == out_bounds

        # Check with gpd.GeoDataFrame
        outlines = gu.Vector(gu.examples.get_path("everest_rgi_outlines"))
        outlines = gu.Vector(outlines.ds.to_crs(img1.crs))  # reproject to img1's CRS
        out_bounds = pt.merge_bounds((img1, outlines.ds))

        assert out_bounds[0] == min(img1.bounds.left, outlines.ds.total_bounds[0])
        assert out_bounds[1] == min(img1.bounds.bottom, outlines.ds.total_bounds[1])
        assert out_bounds[2] == max(img1.bounds.right, outlines.ds.total_bounds[2])
        assert out_bounds[3] == max(img1.bounds.top, outlines.ds.total_bounds[3])
